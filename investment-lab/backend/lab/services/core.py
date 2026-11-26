"""Audit trail, notifications, settings and provider-status helpers."""
from __future__ import annotations

import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AuditEvent, Notification, ProviderStatus, RiskPolicy, UserSettings, utcnow
from ..risk import DEFAULT_LIMITS
from ..strategy_schema import canonical_hash

DEFAULT_USER_SETTINGS = {
    "timezone": "America/Chicago",
    "research_schedule": {"weekday": 0, "hour": 7, "minute": 0},  # Monday 07:00 America/Chicago
    "monthly_review": {"rule": "first_trading_monday", "hour": 8},
    "min_score": 70,
    "max_proposals_per_run": 3,
    "proposal_validity_days": 7,
    "notifications": {"email_enabled": False, "email_to": "", "digest_routine_fills": True},
    "global_pause": False,
    "benchmark": None,
}


def audit(s: Session, user_id: str | None, actor: str, action: str, entity_type: str, entity_id: str | None,
          prior=None, new=None, payload: dict | None = None, correlation_id: str | None = None) -> None:
    s.add(AuditEvent(user_id=user_id, actor=actor, action=action, entity_type=entity_type, entity_id=entity_id,
                     prior_hash=canonical_hash(prior) if prior is not None else None,
                     new_hash=canonical_hash(new) if new is not None else None,
                     payload=payload or {}, correlation_id=correlation_id))


def notify(s: Session, user_id: str, type_: str, title: str, body: str, dedupe_key: str, link: str | None = None,
           severity: str = "info", data_timestamp: datetime | None = None, payload: dict | None = None) -> bool:
    """Insert an in-app notification; deduplicated by (user, dedupe_key). Returns True if new."""
    settings = get_user_settings(s, user_id)
    email = settings.get("notifications", {}).get("email_enabled") and severity == "urgent" and bool(get_settings().smtp_host)
    prefix = "[PAPER TRADING] " if type_ in ("fill", "exit", "drawdown", "order") else ""
    stmt = pg_insert(Notification).values(
        id=__import__("uuid").uuid4().hex, user_id=user_id, type=type_, severity=severity, title=prefix + title,
        body=body, link=link, dedupe_key=dedupe_key, data_timestamp=data_timestamp, payload=payload or {},
        delivery_state="email_pending" if email else "in_app", delivery_attempts=0, created_at=utcnow(),
    ).on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"]).returning(Notification.id)
    return s.execute(stmt).first() is not None


def deliver_pending_emails(s: Session, max_attempts: int = 5) -> dict:
    """Notification delivery runs independently of trade transactions (separate job)."""
    cfg = get_settings()
    rows = s.scalars(select(Notification).where(Notification.delivery_state == "email_pending",
                                                Notification.delivery_attempts < max_attempts).limit(50)).all()
    sent = failed = 0
    for n in rows:
        to = get_user_settings(s, n.user_id).get("notifications", {}).get("email_to") or cfg.notify_email_to
        n.delivery_attempts += 1
        if not (cfg.smtp_host and cfg.smtp_from and to):
            n.delivery_state = "in_app"
            continue
        try:
            msg = EmailMessage()
            msg["Subject"] = n.title
            msg["From"] = cfg.smtp_from
            msg["To"] = to
            ts = n.data_timestamp.isoformat() if n.data_timestamp else "n/a"
            msg.set_content(f"{n.body}\n\nPaper trading only — no real orders. Data timestamp: {ts}\n")
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20) as smtp:
                smtp.starttls()
                if cfg.smtp_user:
                    smtp.login(cfg.smtp_user, cfg.smtp_password)
                smtp.send_message(msg)
            n.delivery_state = "email_sent"
            sent += 1
        except Exception:  # noqa: BLE001
            failed += 1
            if n.delivery_attempts >= max_attempts:
                n.delivery_state = "email_failed"
    return {"sent": sent, "failed": failed}


def get_user_settings(s: Session, user_id: str) -> dict:
    row = s.get(UserSettings, user_id)
    data = dict(DEFAULT_USER_SETTINGS)
    if row:
        for k, v in (row.data or {}).items():
            data[k] = v
    return data


def update_user_settings(s: Session, user_id: str, patch: dict, actor: str) -> dict:
    row = s.get(UserSettings, user_id)
    prior = dict(row.data) if row else {}
    merged = {**prior, **patch}
    if row:
        row.data = merged
        row.updated_at = utcnow()
    else:
        s.add(UserSettings(user_id=user_id, data=merged))
    audit(s, user_id, actor, "settings.update", "settings", user_id, prior, merged, {"keys": sorted(patch)})
    return get_user_settings(s, user_id)


def current_policy(s: Session, user_id: str) -> RiskPolicy:
    pol = s.scalars(select(RiskPolicy).where(RiskPolicy.user_id == user_id).order_by(RiskPolicy.version.desc())).first()
    if pol is None:
        pol = RiskPolicy(user_id=user_id, version=1, limits=dict(DEFAULT_LIMITS))
        s.add(pol)
        s.flush()
    return pol


def policy_by_version(s: Session, user_id: str, version: int) -> RiskPolicy | None:
    return s.scalars(select(RiskPolicy).where(RiskPolicy.user_id == user_id, RiskPolicy.version == version)).first()


def new_policy_version(s: Session, user_id: str, limits_patch: dict, actor: str) -> RiskPolicy:
    """Risk default changes create a new immutable policy version affecting FUTURE proposals only."""
    cur = current_policy(s, user_id)
    allowed = {k: v for k, v in limits_patch.items() if k in DEFAULT_LIMITS}
    pol = RiskPolicy(user_id=user_id, version=cur.version + 1, limits={**cur.limits, **allowed})
    s.add(pol)
    audit(s, user_id, actor, "risk_policy.new_version", "risk_policy", str(pol.version), cur.limits, pol.limits)
    s.flush()
    return pol


def provider_event(s: Session, name: str, kind: str, ok: bool, error: str | None = None, calls: int = 1,
                   quota_note: str | None = None, state: str | None = None) -> None:
    row = s.get(ProviderStatus, name)
    now = datetime.now(timezone.utc)
    if row is None:
        row = ProviderStatus(name=name, kind=kind, state="ok", calls_today=0)
        s.add(row)
    if row.calls_day != now.date():
        row.calls_day, row.calls_today = now.date(), 0
    row.calls_today = (row.calls_today or 0) + calls
    if ok:
        row.state = state or "ok"
        row.last_success_at = now
    else:
        row.state = state or "degraded"
        row.last_error_at = now
        row.last_error = (error or "")[:1000]
    if quota_note:
        row.quota_note = quota_note
    row.updated_at = now
