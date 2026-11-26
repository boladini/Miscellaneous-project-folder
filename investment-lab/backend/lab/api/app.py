"""REST API v1 + SPA hosting. Every resource is ownership-checked server-side (tenant isolation)."""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import secrets
import time as _time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import market_calendar as cal
from ..bootstrap import check_password
from ..config import get_settings
from ..db import get_db
from ..jobs import enqueue, first_trading_monday, schedule_tick
from ..models import (
    AuditEvent, BacktestRun, Critique, Evidence, Experiment, Job, LedgerEntry, Lesson, Notification, Portfolio, Proposal,
    ProviderStatus, QAMessage, ResearchRun, Signal, Source, StrategyVersion, ThesisReview, User, utcnow,
)
from ..providers.http import is_safe_display_url
from ..providers.llm import get_llm
from ..providers.market import get_market_provider
from ..providers.research import get_research_providers
from ..risk import DEFAULT_LIMITS
from ..services import analytics
from ..services.backtests import export_all, reset_lab
from ..services.core import current_policy, get_user_settings, new_policy_version, update_user_settings
from ..services.market_data import data_health
from ..services.portfolios import active_portfolios, benchmark_for, close_experiment, portfolio_state, reconcile, set_pause
from ..services.proposals import DecisionError, decide
from ..strategy_schema import StrategySpec, describe_node, json_schema
from .security import CSRF_COOKIE, SESSION_COOKIE, ApiError, correlation_id, current_user, limiter, make_session_token

log = logging.getLogger("lab.api")
STATIC_DIR = Path(os.environ.get("LAB_STATIC_DIR", Path(__file__).resolve().parents[2] / "static"))


def _default(o):
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    raise TypeError(type(o))


def J(data, status: int = 200) -> Response:
    return Response(json.dumps(data, default=_default), status_code=status, media_type="application/json")


app = FastAPI(title="Autonomous AI Investment Laboratory (paper trading only)", version="1.0.0",
              openapi_url="/api/v1/openapi.json", docs_url="/api/v1/docs")
_origins = [o for o in get_settings().cors_origins.split(",") if o]
if _origins:
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def _mw(request: Request, call_next):
    cid = correlation_id(request)
    t0 = _time.perf_counter()
    resp = await call_next(request)
    resp.headers["x-correlation-id"] = cid
    resp.headers["x-content-type-options"] = "nosniff"
    resp.headers["referrer-policy"] = "same-origin"
    resp.headers["x-frame-options"] = "DENY"
    resp.headers["content-security-policy"] = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                               "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
    if request.url.path.startswith("/api/"):
        resp.headers["cache-control"] = "no-store"
        log.info("%s %s %s %.0fms cid=%s", request.method, request.url.path, resp.status_code, (_time.perf_counter() - t0) * 1000, cid)
    return resp


def _err(request: Request, status: int, code: str, message: str, extra: dict | None = None):
    cid = getattr(request.state, "correlation_id", None)
    return JSONResponse({"error": {"code": code, "message": message, "correlation_id": cid, **(extra or {})}}, status_code=status)


@app.exception_handler(StarletteHTTPException)
async def _http_exc(request: Request, exc: StarletteHTTPException):
    if isinstance(exc.detail, dict):
        return _err(request, exc.status_code, exc.detail.get("code", "error"), exc.detail.get("message", ""))
    return _err(request, exc.status_code, "http_error", str(exc.detail))


@app.exception_handler(RequestValidationError)
async def _val_exc(request: Request, exc: RequestValidationError):
    return _err(request, 422, "validation_error", "request validation failed", {"details": json.loads(json.dumps(exc.errors(), default=str))[:10]})


@app.exception_handler(Exception)
async def _any_exc(request: Request, exc: Exception):
    log.exception("unhandled error")
    return _err(request, 500, "internal_error", "an internal error occurred")


def owned(db: Session, model, id_: str, user: User):
    row = db.get(model, id_)
    if row is None or getattr(row, "user_id", None) != user.id:
        raise ApiError(404, "not_found", f"{model.__name__.lower()} not found")
    return row


def _date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise ApiError(422, "invalid_date", f"invalid date {s!r}")


# ------------------------------------------------------------------------------------------------ auth
class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


@app.get("/api/v1/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok"}


@app.post("/api/v1/auth/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    limiter.check(f"login:{request.client.host if request.client else 'x'}", 10, 300)
    u = db.scalars(select(User).where(User.email == body.email.lower().strip())).first()
    if not u or not check_password(body.password, u.password_hash):
        raise ApiError(401, "invalid_credentials", "invalid email or password")
    csrf = secrets.token_urlsafe(24)
    resp = J({"user": {"id": u.id, "email": u.email}, "csrf_token": csrf})
    secure = get_settings().cookie_secure
    resp.set_cookie(SESSION_COOKIE, make_session_token(u.id), httponly=True, samesite="lax", secure=secure, max_age=14 * 86400, path="/")
    resp.set_cookie(CSRF_COOKIE, csrf, httponly=False, samesite="strict", secure=secure, max_age=14 * 86400, path="/")
    return resp


@app.post("/api/v1/auth/logout")
def logout(user: User = Depends(current_user)):
    resp = J({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    resp.delete_cookie(CSRF_COOKIE, path="/")
    return resp


@app.get("/api/v1/auth/me")
def me(user: User = Depends(current_user)):
    cfg = get_settings()
    return J({"user": {"id": user.id, "email": user.email, "timezone": user.timezone}, "mode": cfg.lab_mode,
              "paper_trading_only": True})


# ------------------------------------------------------------------------------------------------ dashboard
def _system_status(db: Session, user: User) -> dict:
    cfg = get_settings()
    jobs = {}
    for t in ("daily", "research", "monthly", "market_refresh"):
        last_ok = db.scalars(select(Job).where(Job.user_id == user.id, Job.type == t, Job.state == "succeeded")
                             .order_by(Job.finished_at.desc())).first()
        last_any = db.scalars(select(Job).where(Job.user_id == user.id, Job.type == t).order_by(Job.created_at.desc())).first()
        jobs[t] = {"last_success_at": last_ok.finished_at if last_ok else None, "last_success_key": last_ok.schedule_key if last_ok else None,
                   "last_state": last_any.state if last_any else None, "last_error": (last_any.error or "")[:300] if last_any and last_any.state in ("failed", "dead", "queued") and last_any.error else None}
    dead = db.scalar(select(func.count()).select_from(Job).where(Job.user_id == user.id, Job.state == "dead"))
    st = get_user_settings(db, user.id)
    now = utcnow()
    tz = cal.ZoneInfo(st.get("timezone") or cfg.display_timezone)
    local = now.astimezone(tz)
    rs = st.get("research_schedule", {})
    days = (int(rs.get("weekday", 0)) - local.weekday()) % 7
    nxt_r = datetime.combine((local + timedelta(days=days)).date(), datetime.min.time().replace(hour=int(rs.get("hour", 7))), tz)
    if nxt_r <= local:
        nxt_r += timedelta(days=7)
    latest = cal.latest_finalized_session(now, cfg.daily_data_ready_hour_et, cfg.daily_data_ready_minute_et)
    nxt_daily = cal.data_ready_utc(cal.next_session(latest))
    ftm = first_trading_monday(local.year, local.month)
    if ftm < local.date():
        y, m = (local.year + (local.month == 12), local.month % 12 + 1)
        ftm = first_trading_monday(y, m)
    provs = [{"name": p.name, "kind": p.kind, "state": p.state, "last_success_at": p.last_success_at, "last_error_at": p.last_error_at,
              "last_error": (p.last_error or "")[:300], "calls_today": p.calls_today, "quota_note": p.quota_note}
             for p in db.scalars(select(ProviderStatus)).all()]
    mp = get_market_provider()
    ok, why = mp.configured()
    llm = get_llm()
    return {"mode": cfg.lab_mode, "paper_trading_only": True, "global_pause": bool(st.get("global_pause")),
            "market_provider": {"name": mp.name, "configured": ok, "reason": None if ok else why, "synthetic": mp.synthetic,
                                "adjustment_basis": mp.adjustment_basis, "capabilities": mp.capabilities},
            "llm": {"configured": bool(llm), "provider": llm.name if llm else "template", "model": llm.model if llm else None},
            "research_providers": [{"name": p.name, "channel": p.channel, "available": p.available()[0], "reason": p.available()[1]}
                                   for p in get_research_providers()],
            "data": data_health(db), "jobs": jobs, "dead_letter_jobs": dead, "providers": provs,
            "next_runs": {"daily": nxt_daily, "research": nxt_r.astimezone(timezone.utc), "monthly_review_date": ftm},
            "server_time": now}


@app.get("/api/v1/dashboard")
def dashboard(start: str | None = None, end: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    sd, ed = _date(start), _date(end)
    ports = active_portfolios(db, user.id)
    cards = []
    agg: dict[str, dict] = {}
    for p in ports:
        summ = analytics.portfolio_summary(db, p, sd, ed)
        summ["sparkline"] = [x["nav"] for x in analytics.portfolio_series(db, p, sd, ed)][-60:]
        cards.append(summ)
        for x in analytics.portfolio_series(db, p, sd, ed):
            a = agg.setdefault(x["session"], {"session": x["session"], "nav": 0.0, "benchmark_nav": 0.0, "n": 0, "bn": 0})
            a["nav"] += x["nav"]
            a["n"] += 1
            if x["benchmark_nav"] is not None:
                a["benchmark_nav"] += x["benchmark_nav"]
                a["bn"] += 1
    series = [{"session": v["session"], "nav": round(v["nav"], 2), "benchmark_nav": round(v["benchmark_nav"], 2) if v["bn"] == len(ports) else None}
              for k, v in sorted(agg.items()) if v["n"] == len(ports)]
    pending = db.scalar(select(func.count()).select_from(Proposal).where(Proposal.user_id == user.id, Proposal.state == "pending",
                                                                         Proposal.expires_at > utcnow()))
    activity = [{"at": a.occurred_at, "actor": a.actor, "action": a.action, "entity_type": a.entity_type, "entity_id": a.entity_id}
                for a in db.scalars(select(AuditEvent).where(AuditEvent.user_id == user.id).order_by(AuditEvent.id.desc()).limit(15)).all()]
    unread = db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None)))
    return J({"pending_approvals": pending, "aggregate_nav": sum(c["nav"] for c in cards),
              "aggregate_starting_cash": sum(c["starting_cash"] for c in cards), "portfolios": cards, "series": series,
              "activity": activity, "unread_notifications": unread, "system": _system_status(db, user)})


# ------------------------------------------------------------------------------------------------ portfolios
@app.get("/api/v1/portfolios")
def portfolios(start: str | None = None, end: str | None = None, family: str | None = None,
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    sd, ed = _date(start), _date(end)
    ports = [p for p in active_portfolios(db, user.id) if not family or p.family == family]
    rows = [analytics.portfolio_summary(db, p, sd, ed) for p in ports]
    ranked = sorted(rows, key=lambda r: (r["metrics"]["excess_return"] is None, -(r["metrics"]["excess_return"] or 0)))
    exps = []
    for e in db.scalars(select(Experiment).where(Experiment.user_id == user.id).order_by(Experiment.created_at.desc())).all():
        es = analytics.experiment_summary(db, e)
        es["portfolio_family"] = db.get(Portfolio, e.portfolio_id).family
        exps.append(es)
    exps.sort(key=lambda r: (r["insufficient_history"], -(r["return_on_budget"] or 0)))
    return J({"leaderboard": ranked, "experiments": exps,
              "note": "Metrics with fewer than 20 observations are shown as insufficient history, not ranked as zero."})


@app.get("/api/v1/portfolios/{pid}")
def portfolio(pid: str, start: str | None = None, end: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = owned(db, Portfolio, pid, user)
    sd, ed = _date(start), _date(end)
    st = portfolio_state(db, p)
    exps = [analytics.experiment_summary(db, e) for e in db.scalars(select(Experiment).where(Experiment.portfolio_id == p.id)
                                                                     .order_by(Experiment.created_at.desc())).all()]
    b = benchmark_for(db, p.id)
    return J({"portfolio": analytics.portfolio_summary(db, p, sd, ed), "positions": st["positions"], "experiments": exps,
              "trades": analytics.trades(db, portfolio_id=p.id, limit=200), "closed_lots": analytics.closed_lots(db, portfolio_id=p.id),
              "attribution": analytics.attribution(db, p), "reconciliation": reconcile(db, p),
              "benchmark": {"symbol": b.symbol, "entry_session": b.entry_session, "entry_price": b.entry_price, "shares": b.shares,
                            "basis": "price-only (dividends not reinvested)", "inception_at": b.inception_at} if b else None})


@app.get("/api/v1/portfolios/{pid}/series")
def portfolio_series(pid: str, start: str | None = None, end: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = owned(db, Portfolio, pid, user)
    return J({"series": analytics.portfolio_series(db, p, _date(start), _date(end))})


class PauseIn(BaseModel):
    reason: str = Field(default="", max_length=300)


@app.post("/api/v1/portfolios/{pid}/pause")
def pause(pid: str, body: PauseIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = owned(db, Portfolio, pid, user)
    set_pause(db, user.id, p, True, user.id, body.reason)
    db.commit()
    return J({"status": p.status, "circuit_breaker": p.circuit_breaker})


@app.post("/api/v1/portfolios/{pid}/resume")
def resume(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = owned(db, Portfolio, pid, user)
    set_pause(db, user.id, p, False, user.id, "resumed by user")
    db.commit()
    return J({"status": p.status, "circuit_breaker": p.circuit_breaker})


# ------------------------------------------------------------------------------------------------ proposals
def _proposal_out(db: Session, pr: Proposal, full: bool = False) -> dict:
    out = {"id": pr.id, "kind": pr.kind, "state": pr.state, "state_reason": pr.state_reason, "score": pr.score,
           "components": pr.component_scores, "gates": pr.gates, "card": pr.card, "budget": pr.budget, "exploratory": pr.exploratory,
           "expires_at": pr.expires_at, "created_at": pr.created_at, "decided_at": pr.decided_at, "rank": pr.rank,
           "portfolio_id": pr.portfolio_id, "scope_hash": pr.scope_hash, "parent_experiment_id": pr.parent_experiment_id,
           "experiment_id": (db.scalars(select(Experiment.id).where(Experiment.proposal_id == pr.id)).first())}
    if pr.state == "pending" and pr.expires_at <= utcnow():
        out["state"] = "expired"
    if full:
        ver = db.get(StrategyVersion, pr.strategy_version_id)
        spec = StrategySpec.model_validate(ver.rule_json)
        evs = db.scalars(select(Evidence).where(Evidence.id.in_(ver.evidence_ids or []))).all()
        ev_out = []
        for e in evs:
            src = db.get(Source, e.source_id)
            ev_out.append(_evidence_out(e, src))
        bt = db.get(BacktestRun, pr.backtest_run_id) if pr.backtest_run_id else None
        cr = db.get(Critique, pr.critique_id) if pr.critique_id else None
        out.update({"rules": ver.rule_json, "rule_hash": ver.rule_hash, "version": ver.version, "strategy_version_id": ver.id,
                    "rules_text": {"entry": describe_node(spec.entry), "exit": describe_node(spec.exit)},
                    "evidence": ev_out, "rationale": pr.rationale, "uncertainty": pr.uncertainty, "scope": pr.scope,
                    "backtest": _bt_out(bt, series=True) if bt else None, "diff": ver.diff,
                    "critique": {"items": cr.items, "summary": cr.summary, "max_severity": cr.max_severity, "provider": cr.provider,
                                 "note": "Independent model/checklist pass — not human verification."} if cr else None,
                    "qa": [{"question": q.question, "answer": q.answer, "citations": q.citations, "provider": q.provider, "at": q.created_at}
                           for q in db.scalars(select(QAMessage).where(QAMessage.proposal_id == pr.id).order_by(QAMessage.created_at)).all()]})
    return out


def _evidence_out(e: Evidence, src: Source) -> dict:
    return {"id": e.id, "topic": e.topic, "stance": e.stance, "claim": e.claim, "excerpt": e.excerpt, "limitations": e.limitations,
            "quality": e.quality, "quality_label": e.quality_label, "claim_supported": e.claim_supported, "is_new": e.is_new,
            "extraction_method": e.extraction_method, "research_run_id": e.research_run_id, "created_at": e.created_at,
            "source": {"id": src.id, "url": src.url if is_safe_display_url(src.url) else None, "title": src.title, "authors": src.authors,
                       "publisher": src.publisher, "published_at": src.published_at, "retrieved_at": src.retrieved_at,
                       "source_type": src.source_type, "provider": src.provider, "retrieval_id": src.retrieval_id,
                       "url_verified": src.url_verified, "verification_note": src.verification_note, "synthetic": src.synthetic,
                       "cited_by_count": src.cited_by_count}}


def _bt_out(bt: BacktestRun, series: bool = False) -> dict:
    out = {"id": bt.id, "status": bt.status, "reason": bt.reason, "strategy_version_id": bt.strategy_version_id, "rule_hash": bt.rule_hash,
           "metrics": bt.metrics, "parameters": bt.parameters, "limitations": bt.limitations, "code_version": bt.code_version,
           "created_at": bt.created_at, "trigger": bt.trigger,
           "dataset_manifest": {k: v for k, v in (bt.dataset_manifest or {}).items() if k != "instruments"},
           "instrument_count": len((bt.dataset_manifest or {}).get("instruments", {}))}
    if series:
        out["series"] = bt.series
        out["dataset_manifest"] = bt.dataset_manifest
    return out


@app.get("/api/v1/proposals")
def proposals(status: str | None = None, q: str | None = None, limit: int = Query(50, le=200), offset: int = 0,
              user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(Proposal).where(Proposal.user_id == user.id)
    if status == "pending":
        query = query.where(Proposal.state == "pending", Proposal.expires_at > utcnow())
    elif status == "expired":
        query = query.where(or_(Proposal.state == "expired", (Proposal.state == "pending") & (Proposal.expires_at <= utcnow())))
    elif status:
        query = query.where(Proposal.state == status)
    rows = db.scalars(query.order_by(Proposal.created_at.desc(), Proposal.rank)).all()
    out = [_proposal_out(db, p) for p in rows]
    if q:
        ql = q.lower()
        out = [o for o in out if ql in json.dumps(o["card"], default=str).lower()]
    if status == "pending":
        out.sort(key=lambda o: -(o["score"] or 0))
    return J({"items": out[offset:offset + limit], "total": len(out)})


@app.get("/api/v1/proposals/{prid}")
def proposal(prid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return J(_proposal_out(db, owned(db, Proposal, prid, user), full=True))


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(yes|no)$")
    idempotency_key: str = Field(min_length=8, max_length=100)
    expected_scope_hash: str = Field(min_length=64, max_length=64)


@app.post("/api/v1/proposals/{prid}/decision")
def decision(prid: str, body: DecisionIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    limiter.check(f"decide:{user.id}", 30, 60)
    try:
        res = decide(db, user.id, prid, body.decision, body.idempotency_key, body.expected_scope_hash,
                     correlation_id=getattr(request.state, "correlation_id", None))
        db.commit()
        return J(res)
    except DecisionError as e:
        db.commit()  # persist state changes such as expired/blocked
        raise ApiError(e.status, e.code, e.message)


class QuestionIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


@app.post("/api/v1/proposals/{prid}/qa")
def ask(prid: str, body: QuestionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..agents.learning import answer_question

    limiter.check(f"qa:{user.id}", 20, 3600)
    pr = owned(db, Proposal, prid, user)
    msg = answer_question(db, user.id, pr, body.question)
    db.commit()
    return J({"question": msg.question, "answer": msg.answer, "citations": msg.citations, "provider": msg.provider,
              "note": "Answers explain the proposal; they cannot change its rules or scope."})


# ------------------------------------------------------------------------------------------------ experiments
@app.get("/api/v1/experiments/{eid}")
def experiment(eid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = owned(db, Experiment, eid, user)
    ver = db.get(StrategyVersion, e.approved_version_id)
    spec = StrategySpec.model_validate(ver.rule_json)
    pr = db.get(Proposal, e.proposal_id)
    from ..services.portfolios import open_lots

    lots = open_lots(db, e.portfolio_id, e.id)
    st = portfolio_state(db, db.get(Portfolio, e.portfolio_id))
    positions = [x for x in st["positions"] if x["experiment_id"] == e.id]
    timeline = []
    for sgl in db.scalars(select(Signal).where(Signal.experiment_id == e.id).order_by(Signal.created_at.desc()).limit(100)).all():
        timeline.append({"at": sgl.observed_at, "type": f"signal:{sgl.kind}", "session": sgl.session, "result": sgl.result,
                         "detail": sgl.rules_evaluated})
    for a in db.scalars(select(AuditEvent).where(AuditEvent.entity_id.in_([e.id, e.proposal_id])).order_by(AuditEvent.id.desc())).all():
        timeline.append({"at": a.occurred_at, "type": a.action, "actor": a.actor, "detail": a.payload})
    timeline.sort(key=lambda x: str(x["at"]), reverse=True)
    revisions = [_proposal_out(db, p) for p in db.scalars(select(Proposal).where(Proposal.parent_experiment_id == e.id)).all()]
    reviews = [{"session": r.session, "kind": r.kind, "status": r.status, "summary": r.summary, "observations": r.observations,
                "confidence": r.confidence, "counterargument": r.counterargument, "provider": r.provider}
               for r in db.scalars(select(ThesisReview).where(ThesisReview.experiment_id == e.id).order_by(ThesisReview.session.desc()).limit(60)).all()]
    lessons = [_lesson_out(x) for x in db.scalars(select(Lesson).where(Lesson.experiment_id == e.id)).all()]
    evs = []
    for ev in db.scalars(select(Evidence).where(Evidence.id.in_(ver.evidence_ids or []))).all():
        evs.append(_evidence_out(ev, db.get(Source, ev.source_id)))
    bt = db.get(BacktestRun, pr.backtest_run_id) if pr.backtest_run_id else None
    return J({"experiment": analytics.experiment_summary(db, e), "version": {"id": ver.id, "version": ver.version, "rule_hash": ver.rule_hash,
              "rules": ver.rule_json, "rules_text": {"entry": describe_node(spec.entry), "exit": describe_node(spec.exit)},
              "hypothesis": ver.hypothesis, "thesis": ver.thesis, "generated_by": ver.generated_by},
              "proposal": _proposal_out(db, pr), "positions": positions, "open_lot_count": len(lots),
              "trades": analytics.trades(db, experiment_id=e.id), "closed_lots": analytics.closed_lots(db, experiment_id=e.id),
              "timeline": timeline[:150], "revisions": revisions, "reviews": reviews, "lessons": lessons, "evidence": evs,
              "backtest": _bt_out(bt, series=True) if bt else None})


@app.get("/api/v1/experiments/{eid}/trades")
def experiment_trades(eid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = owned(db, Experiment, eid, user)
    return J({"trades": analytics.trades(db, experiment_id=e.id), "closed_lots": analytics.closed_lots(db, experiment_id=e.id)})


@app.get("/api/v1/experiments/{eid}/reviews")
def experiment_reviews(eid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = owned(db, Experiment, eid, user)
    return J({"reviews": [{"session": r.session, "status": r.status, "summary": r.summary} for r in
                          db.scalars(select(ThesisReview).where(ThesisReview.experiment_id == e.id).order_by(ThesisReview.session.desc())).all()],
              "lessons": [_lesson_out(x) for x in db.scalars(select(Lesson).where(Lesson.experiment_id == e.id)).all()]})


class ConfirmIn(BaseModel):
    confirm: str


@app.post("/api/v1/experiments/{eid}/close")
def close_exp(eid: str, body: ConfirmIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = owned(db, Experiment, eid, user)
    if body.confirm != "CLOSE":
        raise ApiError(422, "confirmation_required", 'type "CLOSE" to confirm closing this experiment')
    if e.status in ("closed", "completed"):
        raise ApiError(409, "already_closed", "experiment already closed")
    close_experiment(db, user.id, e, user.id)
    db.commit()
    return J({"status": e.status, "message": "Entries stopped. Open positions will be sold at the next eligible session open "
                                             "after the next daily cycle — no past prices are used."})


# ------------------------------------------------------------------------------------------------ research
@app.get("/api/v1/research/runs")
def research_runs(limit: int = Query(20, le=100), user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(ResearchRun).where(ResearchRun.user_id == user.id).order_by(ResearchRun.started_at.desc()).limit(limit)).all()
    return J({"items": [{"id": r.id, "state": r.state, "mode": r.mode, "llm_provider": r.llm_provider, "llm_model": r.llm_model,
                         "started_at": r.started_at, "finished_at": r.finished_at, "stats": r.stats, "headline": (r.briefing or {}).get("headline"),
                         "cost_usd": r.cost_usd, "error": r.error} for r in rows]})


@app.get("/api/v1/research/runs/{rid}")
def research_run(rid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = owned(db, ResearchRun, rid, user)
    evs = db.scalars(select(Evidence).where(Evidence.research_run_id == r.id)).all()
    return J({"id": r.id, "state": r.state, "mode": r.mode, "llm_provider": r.llm_provider, "llm_model": r.llm_model,
              "prompt_version": r.prompt_version, "started_at": r.started_at, "finished_at": r.finished_at, "queries": r.queries,
              "coverage": r.coverage, "briefing": r.briefing, "stats": r.stats, "cost_usd": r.cost_usd,
              "evidence": [_evidence_out(e, db.get(Source, e.source_id)) for e in evs],
              "proposals": [_proposal_out(db, p) for p in db.scalars(select(Proposal).where(Proposal.research_run_id == r.id)).all()]})


@app.get("/api/v1/evidence")
def evidence(q: str | None = None, topic: str | None = None, stance: str | None = None, quality: str | None = None,
             source_type: str | None = None, start: str | None = None, end: str | None = None,
             limit: int = Query(100, le=500), offset: int = 0, user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(Evidence, Source).join(Source, Source.id == Evidence.source_id).where(Evidence.user_id == user.id)
    if topic:
        query = query.where(Evidence.topic == topic)
    if stance:
        query = query.where(Evidence.stance == stance)
    if quality:
        query = query.where(Evidence.quality_label == quality)
    if source_type:
        query = query.where(Source.source_type == source_type)
    if start:
        query = query.where(Evidence.created_at >= datetime.combine(_date(start), datetime.min.time(), timezone.utc))
    if end:
        query = query.where(Evidence.created_at < datetime.combine(_date(end) + timedelta(days=1), datetime.min.time(), timezone.utc))
    if q:
        like = f"%{q[:100]}%"
        query = query.where(or_(Evidence.claim.ilike(like), Source.title.ilike(like), Source.abstract.ilike(like)))
    rows = db.execute(query.order_by(Evidence.created_at.desc()).offset(offset).limit(limit)).all()
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    return J({"items": [_evidence_out(e, s) for e, s in rows], "total": total})


@app.get("/api/v1/research/rejected")
def rejected_ideas(user: User = Depends(current_user), db: Session = Depends(get_db)):
    out = []
    for r in db.scalars(select(ResearchRun).where(ResearchRun.user_id == user.id).order_by(ResearchRun.started_at.desc()).limit(20)).all():
        for x in (r.briefing or {}).get("rejected", []):
            out.append({**x, "research_run_id": r.id, "at": r.started_at})
    for p in db.scalars(select(Proposal).where(Proposal.user_id == user.id, Proposal.state.in_(("rejected", "expired", "blocked", "withdrawn")))).all():
        out.append({"name": p.card.get("name"), "reason": f"proposal {p.state}: {p.state_reason or ''}", "proposal_id": p.id, "at": p.decided_at or p.created_at, "score": p.score})
    return J({"items": out})


# ------------------------------------------------------------------------------------------------ backtests
@app.get("/api/v1/backtests")
def backtests(limit: int = Query(50, le=200), user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(BacktestRun).where(BacktestRun.user_id == user.id).order_by(BacktestRun.created_at.desc()).limit(limit)).all()
    out = []
    for b in rows:
        ver = db.get(StrategyVersion, b.strategy_version_id)
        o = _bt_out(b)
        o["strategy_name"] = ver.rule_json.get("name")
        o["family"] = ver.rule_json.get("family")
        o["version"] = ver.version
        out.append(o)
    return J({"items": out})


@app.get("/api/v1/backtests/{bid}")
def backtest(bid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = owned(db, BacktestRun, bid, user)
    ver = db.get(StrategyVersion, b.strategy_version_id)
    out = _bt_out(b, series=True)
    out.update({"strategy_name": ver.rule_json.get("name"), "rules": ver.rule_json, "version": ver.version,
                "reproduce": {"rule_hash": b.rule_hash, "code_version": b.code_version, "dataset_fingerprint": (b.dataset_manifest or {}).get("fingerprint"),
                              "parameters": b.parameters, "command": f"python -m lab.cli backtest {ver.id}"}})
    return J(out)


class BacktestIn(BaseModel):
    strategy_version_id: str


@app.post("/api/v1/backtests", status_code=202)
def backtest_create(body: BacktestIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    owned(db, StrategyVersion, body.strategy_version_id, user)
    bucket = utcnow().strftime("%Y%m%d%H")
    job, _ = enqueue(db, user.id, "backtest", f"backtest:{body.strategy_version_id}:{bucket}", {"strategy_version_id": body.strategy_version_id},
                     trigger="manual")
    db.commit()
    return J({"job_id": job.id, "state": job.state, "note": "Backtests have no trade authority."}, 202)


# ------------------------------------------------------------------------------------------------ journal
def _lesson_out(x: Lesson) -> dict:
    return {"id": x.id, "experiment_id": x.experiment_id, "period": x.period, "outcome": x.outcome, "recommendation": x.recommendation,
            "lesson": x.lesson, "competing_explanation": x.competing_explanation, "confidence": x.confidence, "metrics": x.metrics,
            "evidence_links": x.evidence_links, "proposal_id": x.proposal_id, "provider": x.provider, "created_at": x.created_at}


@app.get("/api/v1/journal")
def journal(q: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    exps = db.scalars(select(Experiment).where(Experiment.user_id == user.id).order_by(Experiment.created_at.desc())).all()
    theses = []
    for e in exps:
        ver = db.get(StrategyVersion, e.approved_version_id)
        last = db.scalars(select(ThesisReview).where(ThesisReview.experiment_id == e.id).order_by(ThesisReview.session.desc())).first()
        theses.append({"experiment": analytics.experiment_summary(db, e), "hypothesis": ver.hypothesis, "thesis": ver.thesis,
                       "latest_review": {"session": last.session, "summary": last.summary, "observations": last.observations} if last else None})
    lessons = [_lesson_out(x) for x in db.scalars(select(Lesson).where(Lesson.user_id == user.id).order_by(Lesson.created_at.desc())).all()]
    closed = []
    for p in active_portfolios(db, user.id):
        closed += analytics.closed_lots(db, portfolio_id=p.id)
    timeline = [{"at": a.occurred_at, "action": a.action, "actor": a.actor, "entity_type": a.entity_type, "entity_id": a.entity_id, "payload": a.payload}
                for a in db.scalars(select(AuditEvent).where(AuditEvent.user_id == user.id).order_by(AuditEvent.id.desc()).limit(300)).all()]
    if q:
        ql = q.lower()
        lessons = [x for x in lessons if ql in json.dumps(x, default=str).lower()]
        timeline = [x for x in timeline if ql in json.dumps(x, default=str).lower()]
        theses = [x for x in theses if ql in json.dumps(x, default=str).lower()]
    return J({"theses": theses, "lessons": lessons, "closed_trades": closed, "timeline": timeline})


# ------------------------------------------------------------------------------------------------ notifications
@app.get("/api/v1/notifications")
def notifications(unread: bool | None = None, type: str | None = None, limit: int = Query(100, le=500), offset: int = 0,
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(Notification).where(Notification.user_id == user.id)
    if unread is True:
        query = query.where(Notification.read_at.is_(None))
    if unread is False:
        query = query.where(Notification.read_at.is_not(None))
    if type:
        query = query.where(Notification.type == type)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Notification.created_at.desc()).offset(offset).limit(limit)).all()
    return J({"items": [{"id": n.id, "type": n.type, "severity": n.severity, "title": n.title, "body": n.body, "link": n.link,
                         "created_at": n.created_at, "read_at": n.read_at, "data_timestamp": n.data_timestamp,
                         "delivery_state": n.delivery_state} for n in rows], "total": total,
              "unread": db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None)))})


@app.post("/api/v1/notifications/{nid}/read")
def notif_read(nid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    n = owned(db, Notification, nid, user)
    if n.read_at is None:
        n.read_at = utcnow()
    db.commit()
    return J({"id": n.id, "read_at": n.read_at})


@app.post("/api/v1/notifications/read-all")
def notif_read_all(user: User = Depends(current_user), db: Session = Depends(get_db)):
    for n in db.scalars(select(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))).all():
        n.read_at = utcnow()
    db.commit()
    return J({"ok": True})


# ------------------------------------------------------------------------------------------------ jobs
def _job_out(j: Job) -> dict:
    return {"id": j.id, "type": j.type, "schedule_key": j.schedule_key, "state": j.state, "attempts": j.attempts,
            "max_attempts": j.max_attempts, "trigger": j.trigger, "run_after": j.run_after, "started_at": j.started_at,
            "finished_at": j.finished_at, "progress": j.progress, "result": j.result, "error": (j.error or "")[:1500] or None,
            "created_at": j.created_at}


@app.post("/api/v1/jobs/research", status_code=202)
def job_research(user: User = Depends(current_user), db: Session = Depends(get_db)):
    limiter.check(f"research:{user.id}", 3, 3600)
    bucket = utcnow().strftime("%Y%m%dT%H")
    job, new = enqueue(db, user.id, "research", f"research:manual:{bucket}", {}, trigger="manual", max_attempts=2)
    db.commit()
    return J({"job_id": job.id, "state": job.state, "created": new,
              "note": None if new else "A manual research run already ran or is queued this hour (rate limited)."}, 202)


@app.post("/api/v1/jobs/market-refresh", status_code=202)
def job_refresh(user: User = Depends(current_user), db: Session = Depends(get_db)):
    limiter.check(f"refresh:{user.id}", 6, 3600)
    now = utcnow()
    bucket = now.strftime("%Y%m%dT%H") + f"{now.minute // 10}"
    job, new = enqueue(db, user.id, "market_refresh", f"refresh:{bucket}", {}, trigger="manual", max_attempts=2)
    db.commit()
    return J({"job_id": job.id, "state": job.state, "created": new}, 202)


@app.get("/api/v1/jobs")
def jobs_list(limit: int = Query(50, le=200), user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc()).limit(limit)).all()
    return J({"items": [_job_out(j) for j in rows]})


@app.get("/api/v1/jobs/{jid}")
def job_get(jid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return J(_job_out(owned(db, Job, jid, user)))


# ------------------------------------------------------------------------------------------------ settings/admin
class SettingsPatch(BaseModel):
    timezone: str | None = None
    research_schedule: dict | None = None
    monthly_review: dict | None = None
    min_score: float | None = Field(default=None, ge=0, le=100)
    max_proposals_per_run: int | None = Field(default=None, ge=0, le=3)
    proposal_validity_days: int | None = Field(default=None, ge=1, le=30)
    notifications: dict | None = None
    risk_limits: dict | None = None


@app.get("/api/v1/settings")
def settings_get(user: User = Depends(current_user), db: Session = Depends(get_db)):
    cfg = get_settings()
    pol = current_policy(db, user.id)
    db.commit()
    return J({"settings": get_user_settings(db, user.id), "risk_policy": {"version": pol.version, "limits": pol.limits,
                                                                          "defaults": DEFAULT_LIMITS},
              "providers": {"mode": cfg.lab_mode, "market_provider": cfg.market_provider if not cfg.is_demo else "demo-synthetic",
                            "tiingo_key_configured": bool(cfg.tiingo_api_key), "alphavantage_key_configured": bool(cfg.alphavantage_api_key),
                            "anthropic_key_configured": bool(cfg.anthropic_api_key), "llm_model": cfg.llm_model,
                            "sec_user_agent_configured": bool(cfg.sec_user_agent), "reddit_configured": bool(cfg.reddit_client_id),
                            "x_configured": bool(cfg.x_bearer_token), "smtp_configured": bool(cfg.smtp_host and cfg.smtp_from),
                            "universe": get_market_provider().universe(), "benchmark": get_market_provider().benchmark(),
                            "note": "Credentials are configured only via server environment variables / secret store; never in the browser."},
              "strategy_schema": json_schema()})


@app.patch("/api/v1/settings")
def settings_patch(body: SettingsPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    patch = body.model_dump(exclude_none=True)
    limits = patch.pop("risk_limits", None)
    if "timezone" in patch:
        try:
            cal.ZoneInfo(patch["timezone"])
        except Exception:  # noqa: BLE001
            raise ApiError(422, "invalid_timezone", "unknown IANA timezone")
    if "research_schedule" in patch:
        rs = patch["research_schedule"]
        if not (0 <= int(rs.get("weekday", 0)) <= 6 and 0 <= int(rs.get("hour", 7)) <= 23):
            raise ApiError(422, "invalid_schedule", "weekday 0-6, hour 0-23")
    if patch:
        update_user_settings(db, user.id, patch, user.id)
    pol = None
    if limits:
        bad = [k for k in limits if k not in DEFAULT_LIMITS]
        if bad:
            raise ApiError(422, "invalid_limits", f"unknown risk limits: {bad}")
        pol = new_policy_version(db, user.id, limits, user.id)
        for p in active_portfolios(db, user.id):
            p.policy_version = pol.version  # applies to FUTURE entries/proposals; approved scopes keep their snapshot
    db.commit()
    return J({"settings": get_user_settings(db, user.id), "risk_policy_version": pol.version if pol else current_policy(db, user.id).version,
              "note": "Changes affect future proposals; already-approved strategies keep their approved scope."})


class PauseAllIn(BaseModel):
    paused: bool


@app.post("/api/v1/admin/global-pause")
def global_pause(body: PauseAllIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    update_user_settings(db, user.id, {"global_pause": body.paused}, user.id)
    db.commit()
    return J({"global_pause": body.paused, "note": "Emergency pause stops NEW entries in all portfolios; approved exits continue."})


@app.post("/api/v1/admin/reset")
def admin_reset(body: ConfirmIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.confirm != "RESET":
        raise ApiError(422, "confirmation_required", 'type "RESET" to archive all portfolios and start a new inception')
    new = reset_lab(db, user.id, user.id)
    db.commit()
    return J({"portfolios": [p.id for p in new], "note": "Previous portfolios archived; history preserved."})


@app.get("/api/v1/export")
def export(format: str = "json", table: str = "ledger", user: User = Depends(current_user), db: Session = Depends(get_db)):
    data = export_all(db, user.id)
    if format == "json":
        return Response(json.dumps(data, default=_default, indent=1), media_type="application/json",
                        headers={"content-disposition": f'attachment; filename="investment-lab-export-{date.today()}.json"'})
    if table not in data or not isinstance(data[table], list):
        raise ApiError(422, "invalid_table", f"table must be one of {[k for k, v in data.items() if isinstance(v, list)]}")
    rows = data[table]
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v, default=_default) if isinstance(v, (dict, list)) else v) for k, v in r.items()})
    return Response(buf.getvalue(), media_type="text/csv", headers={"content-disposition": f'attachment; filename="{table}-{date.today()}.csv"'})


@app.get("/api/v1/system/status")
def system_status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    out = _system_status(db, user)
    out["reconciliation"] = {p.family: reconcile(db, p) for p in active_portfolios(db, user.id)}
    out["ledger_entries"] = db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.user_id == user.id))
    return J(out)


# ------------------------------------------------------------------------------------------------ cron (managed scheduler option)
@app.post("/internal/cron/tick")
def cron_tick(request: Request, db: Session = Depends(get_db)):
    secret = get_settings().cron_secret
    sent = request.headers.get("x-cron-secret", "")
    if not secret or not secrets.compare_digest(sent, secret):
        raise ApiError(403, "forbidden", "invalid cron secret")
    res = schedule_tick(db)
    from ..jobs import claim, run_job

    ran = []
    t0 = _time.monotonic()
    while _time.monotonic() - t0 < 45:
        job = claim(db)
        if not job:
            break
        run_job(job.id)
        ran.append(job.schedule_key)
    return J({"scheduled": res["created"], "ran": ran})


# ------------------------------------------------------------------------------------------------ SPA
@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/") or path.startswith("internal/"):
        raise ApiError(404, "not_found", "no such endpoint")
    f = (STATIC_DIR / path).resolve()
    if path and f.is_file() and STATIC_DIR.resolve() in f.parents:
        return FileResponse(f)
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index, headers={"cache-control": "no-cache"})
    return JSONResponse({"message": "Frontend not built. Run `npm run build` in frontend/."}, status_code=200)
