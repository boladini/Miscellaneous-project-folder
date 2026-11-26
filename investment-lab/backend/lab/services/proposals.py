"""Proposal lifecycle and the binary approval decision.

A Yes stores an immutable approval and authorises ONLY the specified strategy version, budget,
universe, rules, horizon and risk limits (the scope, hashed). A No records rejection and creates no
orders. Decisions are idempotent (idempotency key) and transactional (row lock).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import market_calendar as cal
from ..db import advisory_xact_lock
from ..models import Approval, Experiment, Portfolio, Proposal, StrategyVersion, utcnow
from ..strategy_schema import StrategySpec, canonical_hash
from .core import audit, current_policy, notify
from .engine import EXPERIMENT_LIFETIME_SESSIONS
from .portfolios import portfolio_state


class DecisionError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def build_scope(version: StrategyVersion, portfolio: Portfolio, budget: Decimal, policy_version: int, limits: dict,
                exploratory: bool, kind: str = "new", legacy_treatment: str | None = None) -> dict:
    spec = StrategySpec.model_validate(version.rule_json)
    return {
        "strategy_version_id": version.id, "rule_hash": version.rule_hash, "portfolio_id": portfolio.id,
        "budget": str(budget), "universe": spec.universe.model_dump(), "horizon": spec.horizon.value,
        "max_holding_sessions": spec.holding.max_sessions, "allocation": spec.allocation.model_dump(),
        "risk_policy_version": policy_version, "risk_limits": limits, "exploratory": exploratory,
        "authorization_sessions": EXPERIMENT_LIFETIME_SESSIONS, "kind": kind, "legacy_treatment": legacy_treatment,
        "paper_trading_only": True,
    }


def committed_budgets(s: Session, portfolio_id: str) -> Decimal:
    rows = s.scalars(select(Experiment).where(Experiment.portfolio_id == portfolio_id,
                                              Experiment.status.in_(("active", "paused")))).all()
    return sum((e.budget for e in rows), Decimal(0))


def feasible_budget(s: Session, p: Portfolio, fraction: float, limits: dict) -> Decimal:
    nav = portfolio_state(s, p)["nav"]
    want = (nav * Decimal(str(min(fraction, limits.get("max_experiment_fraction", 0.30))))).quantize(Decimal("0.01"))
    room = nav * (1 - Decimal(str(limits.get("min_cash_fraction", 0.10)))) - committed_budgets(s, p.id)
    return max(Decimal(0), min(want, room.quantize(Decimal("0.01"))))


def expire_stale(s: Session, user_id: str, now: datetime | None = None) -> int:
    now = now or utcnow()
    n = 0
    for pr in s.scalars(select(Proposal).where(Proposal.user_id == user_id, Proposal.state == "pending",
                                               Proposal.expires_at <= now)).all():
        pr.state, pr.state_reason = "expired", "validity window (7 calendar days by default) elapsed without decision"
        audit(s, user_id, "system", "proposal.expire", "proposal", pr.id, {"state": "pending"}, {"state": "expired"})
        n += 1
    return n


def decide(s: Session, user_id: str, proposal_id: str, decision: str, idempotency_key: str, expected_scope_hash: str,
           now: datetime | None = None, correlation_id: str | None = None) -> dict:
    if decision not in ("yes", "no"):
        raise DecisionError("invalid_decision", "decision must be 'yes' or 'no'", 422)
    if not idempotency_key or len(idempotency_key) > 100:
        raise DecisionError("invalid_idempotency_key", "idempotency key required (<=100 chars)", 422)
    now = now or utcnow()
    advisory_xact_lock(s, f"engine:{user_id}")
    prev = s.scalars(select(Approval).where(Approval.idempotency_key == idempotency_key)).first()
    if prev:
        if prev.proposal_id != proposal_id or prev.user_id != user_id:
            raise DecisionError("idempotency_conflict", "idempotency key already used for a different request")
        return _result(s, s.get(Proposal, proposal_id), prev, replay=True)
    pr = s.scalars(select(Proposal).where(Proposal.id == proposal_id, Proposal.user_id == user_id).with_for_update()).first()
    if pr is None:
        raise DecisionError("not_found", "proposal not found", 404)
    existing = s.scalars(select(Approval).where(Approval.proposal_id == pr.id)).first()
    if existing:
        if existing.decision == decision:
            return _result(s, pr, existing, replay=True)
        raise DecisionError("already_decided", f"proposal already decided: {existing.decision}")
    if pr.state != "pending":
        raise DecisionError("not_pending", f"proposal is {pr.state}")
    if pr.expires_at <= now:
        pr.state, pr.state_reason = "expired", "expired before decision"
        audit(s, user_id, "system", "proposal.expire", "proposal", pr.id, {"state": "pending"}, {"state": "expired"})
        s.flush()
        raise DecisionError("expired", "proposal expired; a fresh proposal is required")
    if expected_scope_hash != pr.scope_hash or canonical_hash(pr.scope) != pr.scope_hash:
        raise DecisionError("scope_mismatch", "the proposal scope changed; reload and review before deciding")

    if decision == "yes":
        _revalidate(s, pr)  # may raise -> blocked, requires fresh approval
    appr = Approval(user_id=user_id, proposal_id=pr.id, decision=decision, actor_id=user_id, decided_at=now,
                    scope_hash=pr.scope_hash, idempotency_key=idempotency_key)
    s.add(appr)
    pr.decided_at = now
    if decision == "no":
        pr.state = "rejected"
        audit(s, user_id, user_id, "proposal.reject", "proposal", pr.id, {"state": "pending"}, {"state": "rejected"},
              correlation_id=correlation_id)
    else:
        pr.state = "approved"
        _apply_approval(s, user_id, pr, now)
        audit(s, user_id, user_id, "proposal.approve", "proposal", pr.id, {"state": "pending"},
              {"state": "approved", "scope_hash": pr.scope_hash}, correlation_id=correlation_id)
    s.flush()
    return _result(s, pr, appr, replay=False)


def _revalidate(s: Session, pr: Proposal) -> None:
    p = s.get(Portfolio, pr.portfolio_id)
    if pr.kind in ("new", "modification"):
        limits = pr.scope.get("risk_limits", {})
        room = portfolio_state(s, p)["nav"] * (1 - Decimal(str(limits.get("min_cash_fraction", 0.10)))) - committed_budgets(s, p.id)
        if pr.kind == "modification" and pr.parent_experiment_id:
            parent = s.get(Experiment, pr.parent_experiment_id)
            if parent:
                room += parent.budget
        if Decimal(pr.scope["budget"]) > room + Decimal("0.01"):
            pr.state, pr.state_reason = "blocked", (f"constraints changed: only ${room:.2f} of risk budget remains; "
                                                    "a fresh proposal is required")
            s.flush()
            raise DecisionError("blocked", pr.state_reason)
        if p.status == "archived":
            pr.state, pr.state_reason = "blocked", "portfolio archived"
            s.flush()
            raise DecisionError("blocked", pr.state_reason)


def _apply_approval(s: Session, user_id: str, pr: Proposal, now: datetime) -> None:
    version = s.get(StrategyVersion, pr.strategy_version_id)
    p = s.get(Portfolio, pr.portfolio_id)
    if pr.kind in ("pause", "retire", "continue"):
        exp = s.get(Experiment, pr.parent_experiment_id)
        if exp and pr.kind == "pause":
            exp.entries_allowed, exp.status, exp.status_reason = False, "paused", "paused via approved review proposal"
        elif exp and pr.kind == "retire":
            exp.entries_allowed, exp.status, exp.status_reason = False, "legacy", "retired via approved review; exits under original rules"
        notify(s, user_id, "experiment", f"Approved: {pr.kind} {exp.name if exp else ''}", "Change applied.",
               f"apply:{pr.id}", f"/experiments/{pr.parent_experiment_id}")
        return
    first_signal = cal.first_session_with_close_after(now)
    ends_by = cal.add_sessions(first_signal, EXPERIMENT_LIFETIME_SESSIONS)
    exp = Experiment(user_id=user_id, portfolio_id=p.id, proposal_id=pr.id, approved_version_id=version.id,
                     name=pr.card.get("name", "Experiment"), budget=Decimal(pr.scope["budget"]), status="active",
                     entries_allowed=True, thesis_status="untested", approved_at=now, ends_by=ends_by,
                     successor_of=pr.parent_experiment_id)
    s.add(exp)
    s.flush()
    if pr.kind == "modification" and pr.parent_experiment_id:
        parent = s.get(Experiment, pr.parent_experiment_id)
        if parent:
            parent.entries_allowed = False
            parent.status = "legacy"
            parent.status_reason = f"superseded by {exp.id}; legacy holdings managed under original rules until exit"
    notify(s, user_id, "approval", f"Approved — awaiting eligible market data/signal: {exp.name}",
           f"Entry rules are first evaluated on the {first_signal} close; any simulated fill occurs at a later session open. "
           "No trade has been made yet.", f"approved:{pr.id}", f"/experiments/{exp.id}")


def _result(s: Session, pr: Proposal, appr: Approval, replay: bool) -> dict:
    exp = s.scalars(select(Experiment).where(Experiment.proposal_id == pr.id)).first()
    msg = {"approved": "Approved — awaiting eligible market data/signal. No simulated trade has occurred yet.",
           "rejected": "Rejected. No orders were created; the proposal remains searchable in history."}.get(pr.state, pr.state)
    return {"proposal_id": pr.id, "state": pr.state, "decision": appr.decision, "decided_at": appr.decided_at.isoformat(),
            "experiment_id": exp.id if exp else None, "message": msg, "idempotent_replay": replay}


def create_proposal(s: Session, user_id: str, version: StrategyVersion, portfolio: Portfolio, *, score: float | None,
                    components: dict, gates: dict, rationale: dict, uncertainty: dict, card: dict, budget: Decimal,
                    exploratory: bool, research_run_id: str | None, backtest_run_id: str | None, critique_id: str | None,
                    kind: str = "new", parent_experiment_id: str | None = None, legacy_treatment: str | None = None,
                    validity_days: int = 7, rank: int | None = None) -> Proposal:
    pol = current_policy(s, user_id)
    scope = build_scope(version, portfolio, budget, pol.version, pol.limits, exploratory, kind, legacy_treatment)
    pr = Proposal(user_id=user_id, strategy_version_id=version.id, portfolio_id=portfolio.id, research_run_id=research_run_id,
                  backtest_run_id=backtest_run_id, critique_id=critique_id, kind=kind, parent_experiment_id=parent_experiment_id,
                  legacy_treatment=legacy_treatment, score=score, component_scores=components, gates=gates,
                  rationale=rationale, uncertainty=uncertainty, card=card, budget=budget, scope=scope,
                  scope_hash=canonical_hash(scope), exploratory=exploratory, state="pending",
                  expires_at=datetime.now(timezone.utc) + timedelta(days=validity_days), rank=rank)
    s.add(pr)
    s.flush()
    audit(s, user_id, "research_agent", "proposal.create", "proposal", pr.id, None, {"scope_hash": pr.scope_hash, "score": score})
    return pr
