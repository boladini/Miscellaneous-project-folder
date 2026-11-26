"""PostgreSQL-backed durable job queue + scheduler.

- Unique (user_id, schedule_key) prevents duplicate scheduled/manual jobs.
- Claims use FOR UPDATE SKIP LOCKED with a lease; expired leases are reclaimed after crashes.
- Failures retry with exponential backoff up to max_attempts, then move to 'dead' (dead-letter) and
  raise an operational alert. Research failure never blocks daily risk monitoring (separate jobs).
- Startup catch-up enqueues missed daily sessions in chronological order; catch-up runs fill only
  previously queued orders at recorded opens and never generate retroactive signals.
"""
from __future__ import annotations

import logging
import os
import socket
import traceback
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from . import market_calendar as cal
from .config import get_settings
from .db import SessionLocal
from .models import Job, Portfolio, User, utcnow
from .services.core import audit, deliver_pending_emails, get_user_settings, notify

log = logging.getLogger("lab.jobs")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
TYPES = ("research", "daily", "monthly", "market_refresh", "backtest")


def enqueue(s: Session, user_id: str, type_: str, schedule_key: str, payload: dict | None = None,
            run_after: datetime | None = None, trigger: str = "schedule", max_attempts: int = 3) -> tuple[Job, bool]:
    res = s.execute(pg_insert(Job).values(
        id=__import__("uuid").uuid4().hex, user_id=user_id, type=type_, schedule_key=schedule_key, state="queued", attempts=0,
        max_attempts=max_attempts, run_after=run_after or utcnow(), payload=payload or {}, progress={}, trigger=trigger,
        created_at=utcnow()).on_conflict_do_nothing(index_elements=["user_id", "schedule_key"]).returning(Job.id)).first()
    job = s.scalars(select(Job).where(Job.user_id == user_id, Job.schedule_key == schedule_key)).one()
    return job, res is not None


def claim(s: Session, worker_id: str = WORKER_ID, lease_seconds: int | None = None) -> Job | None:
    lease = lease_seconds or get_settings().job_lease_seconds
    row = s.execute(text("""
        UPDATE jobs SET state='running', lease_owner=:w, lease_expires_at=now() + make_interval(secs => :lease),
               attempts = attempts + 1, started_at = now()
        WHERE id = (
            SELECT id FROM jobs
            WHERE (state='queued' AND run_after <= now()) OR (state='running' AND lease_expires_at < now())
            ORDER BY run_after, created_at
            FOR UPDATE SKIP LOCKED LIMIT 1)
        RETURNING id"""), {"w": worker_id, "lease": lease}).first()
    s.commit()
    return s.get(Job, row[0]) if row else None


def _set_progress(job_id: str, p: dict) -> None:
    with SessionLocal() as s2:
        j = s2.get(Job, job_id)
        if j:
            j.progress = p
            j.lease_expires_at = utcnow() + timedelta(seconds=get_settings().job_lease_seconds)
            s2.commit()


def run_job(job_id: str) -> None:
    from .agents.learning import run_monthly
    from .agents.research import run_research
    from .services.engine import run_daily
    from .services.market_data import ingest
    from .services.proposals import expire_stale

    s = SessionLocal()
    try:
        job = s.get(Job, job_id)
        uid = job.user_id
        if job.type == "daily":
            d = datetime.fromisoformat(job.payload["session"]).date()
            ing = ingest(s)
            s.commit()
            summary = run_daily(s, uid, d)
            summary["ingest"] = ing
            if ing.get("status") in ("failed", "unconfigured") or ing.get("errors"):
                notify(s, uid, "stale_data", "Market data refresh problem",
                       f"Ingestion status {ing.get('status')}: {'; '.join(ing.get('errors', [])[:3]) or ing.get('reason', '')}. "
                       "Entries and fills for affected instruments are suspended.", f"ingest:{d}", "/settings", "urgent")
            result = summary
        elif job.type == "research":
            ing = ingest(s)
            s.commit()
            run = run_research(s, uid, job_id=job.id, progress=lambda p: _set_progress(job_id, p))
            result = {"research_run_id": run.id, "stats": run.stats, "ingest": {k: ing.get(k) for k in ("status", "inserted")}}
        elif job.type == "monthly":
            last = cal.latest_finalized_session(utcnow())
            dep = s.scalars(select(Job).where(Job.user_id == uid, Job.schedule_key == f"daily:{last.isoformat()}")).first()
            if dep and dep.state not in ("succeeded", "dead"):
                job.state, job.run_after = "queued", utcnow() + timedelta(minutes=15)
                job.attempts -= 1
                job.progress = {"step": f"waiting for daily {last} to finish (explicit dependency)"}
                s.commit()
                return
            result = run_monthly(s, uid, job.payload.get("period"))
        elif job.type == "market_refresh":
            result = ingest(s)
        elif job.type == "backtest":
            from .services.backtests import manual_backtest

            result = manual_backtest(s, uid, job.payload["strategy_version_id"])
        else:
            raise ValueError(f"unknown job type {job.type}")
        expire_stale(s, uid)
        job = s.get(Job, job_id)
        job.state, job.finished_at, job.result, job.error, job.lease_owner = "succeeded", utcnow(), _jsonable(result), None, None
        s.commit()
    except Exception as e:  # noqa: BLE001
        s.rollback()
        tb = traceback.format_exc()
        log.exception("job %s failed", job_id)
        with SessionLocal() as s2:
            j = s2.get(Job, job_id)
            if j.attempts >= j.max_attempts:
                j.state = "dead"
                notify(s2, j.user_id, "job_failure", f"Background job failed permanently: {j.type}",
                       f"{type(e).__name__}: {str(e)[:300]}. Moved to the dead-letter queue after {j.attempts} attempts.",
                       f"dead:{j.id}", "/settings", "urgent")
            else:
                j.state = "queued"
                j.run_after = utcnow() + timedelta(seconds=60 * (2 ** j.attempts))
            j.error = f"{type(e).__name__}: {e}\n{tb[-2000:]}"
            j.lease_owner = None
            j.finished_at = utcnow()
            audit(s2, j.user_id, "worker", "job.failed", "job", j.id, None, {"attempts": j.attempts, "state": j.state})
            s2.commit()
    finally:
        s.close()


def _jsonable(x):
    import json

    return json.loads(json.dumps(x, default=str))


# ---------------------------------------------------------------------------------------------
def first_trading_monday(year: int, month: int):
    from datetime import date

    d = date(year, month, 1)
    while d.weekday() != 0:
        d += timedelta(days=1)
    while not cal.is_session(d):
        d = cal.next_session(d)
    return d


def schedule_tick(s: Session, now: datetime | None = None) -> dict:
    """Enqueue due jobs for every user. Safe to call repeatedly (idempotent keys)."""
    now = now or utcnow()
    cfg = get_settings()
    created = []
    for user in s.scalars(select(User)).all():
        st = get_user_settings(s, user.id)
        tz = cal.ZoneInfo(st.get("timezone") or cfg.display_timezone)
        ports = s.scalars(select(Portfolio).where(Portfolio.user_id == user.id, Portfolio.status != "archived")).all()
        if not ports:
            continue
        inception = min(p.inception_at for p in ports)
        latest = cal.latest_finalized_session(now, cfg.daily_data_ready_hour_et, cfg.daily_data_ready_minute_et)
        start = cal.first_session_with_close_after(inception)
        done_keys = set(s.scalars(select(Job.schedule_key).where(Job.user_id == user.id, Job.type == "daily")).all())
        sessions = [d for d in cal.sessions_between(start, latest) if f"daily:{d.isoformat()}" not in done_keys][-30:]
        for d in sessions:
            trig = "catchup" if d < latest else "schedule"
            j, new = enqueue(s, user.id, "daily", f"daily:{d.isoformat()}", {"session": d.isoformat()},
                             run_after=cal.data_ready_utc(d, cfg.daily_data_ready_hour_et, cfg.daily_data_ready_minute_et),
                             trigger=trig, max_attempts=5)
            if new:
                created.append(j.schedule_key)
        # weekly research
        rs = st.get("research_schedule", {})
        local = now.astimezone(tz)
        week_start = (local - timedelta(days=(local.weekday() - int(rs.get("weekday", 0))) % 7)).date()
        due = datetime.combine(week_start, time(int(rs.get("hour", 7)), int(rs.get("minute", 0))), tz)
        if now >= due.astimezone(timezone.utc) and due.astimezone(timezone.utc) >= inception - timedelta(days=7):
            y, w, _ = week_start.isocalendar()
            j, new = enqueue(s, user.id, "research", f"research:{y}-W{w:02d}", {}, run_after=due.astimezone(timezone.utc))
            if new:
                created.append(j.schedule_key)
        # monthly learning review (first trading Monday)
        ftm = first_trading_monday(local.year, local.month)
        mdue = datetime.combine(ftm, time(int(st.get("monthly_review", {}).get("hour", 8)), 0), tz).astimezone(timezone.utc)
        if now >= mdue and mdue >= inception:
            j, new = enqueue(s, user.id, "monthly", f"monthly:{local.year}-{local.month:02d}",
                             {"period": f"{local.year}-{local.month:02d}"}, run_after=mdue)
            if new:
                created.append(j.schedule_key)
        # operational alert: missed daily run
        stuck = s.scalars(select(Job).where(Job.user_id == user.id, Job.type == "daily", Job.state == "queued",
                                            Job.run_after < now - timedelta(hours=6))).first()
        if stuck:
            notify(s, user.id, "job_failure", "Daily run is overdue", f"{stuck.schedule_key} has not run for 6+ hours. Is the worker running?",
                   f"overdue:{stuck.schedule_key}", "/settings", "urgent")
    s.commit()
    return {"created": created}


def worker_loop(max_iterations: int | None = None) -> None:
    import time as _t

    cfg = get_settings()
    i = 0
    log.info("worker %s started", WORKER_ID)
    while max_iterations is None or i < max_iterations:
        i += 1
        try:
            with SessionLocal() as s:
                got = s.execute(text("SELECT pg_try_advisory_lock(hashtext('lab-scheduler'))")).scalar()
                if got:
                    try:
                        schedule_tick(s)
                        deliver_pending_emails(s)
                        s.commit()
                    finally:
                        s.execute(text("SELECT pg_advisory_unlock(hashtext('lab-scheduler'))"))
                        s.commit()
            while True:
                with SessionLocal() as s:
                    job = claim(s)
                if job is None:
                    break
                log.info("running job %s %s", job.type, job.schedule_key)
                run_job(job.id)
        except Exception:  # noqa: BLE001 — keep the worker alive
            log.exception("worker iteration failed")
        _t.sleep(cfg.worker_poll_seconds)
