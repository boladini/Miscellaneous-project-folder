"""Manual backtest reruns (no trade authority), export and archival reset."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..backtest import full_evaluation
from ..db import advisory_xact_lock
from ..models import (
    Approval,
    AuditEvent,
    BacktestRun,
    Critique,
    Evidence,
    Experiment,
    LedgerEntry,
    Lesson,
    Notification,
    Order,
    Portfolio,
    PositionLot,
    Proposal,
    ResearchRun,
    Source,
    StrategyVersion,
    ThesisReview,
    ValuationSnapshot,
)
from ..strategy_schema import StrategySpec
from .core import audit, current_policy, notify
from .market_data import load_dataset
from .portfolios import init_portfolios


def manual_backtest(s: Session, user_id: str, version_id: str) -> dict:
    ver = s.scalars(select(StrategyVersion).where(StrategyVersion.id == version_id, StrategyVersion.user_id == user_id)).one()
    spec = StrategySpec.model_validate(ver.rule_json)
    bt = full_evaluation(spec, load_dataset(s), Decimal(10000), current_policy(s, user_id).limits)
    run = BacktestRun(user_id=user_id, strategy_version_id=ver.id, rule_hash=ver.rule_hash, status=bt["status"], reason=bt.get("reason"),
                      parameters=bt.get("parameters", {}), dataset_manifest=bt.get("dataset_manifest", {}),
                      code_version=bt.get("code_version", "n/a"),
                      metrics={"metrics": bt.get("metrics", {}), "robustness": bt.get("robustness", {}), "robust": bt.get("robust")},
                      series=bt.get("series", {}), limitations=bt.get("limitations", []), trigger="manual")
    s.add(run)
    s.flush()
    return {"backtest_run_id": run.id, "status": run.status}


def _rows(s, model, user_id, attr="user_id"):
    return [_d(r) for r in s.scalars(select(model).where(getattr(model, attr) == user_id)).all()]


def _d(row) -> dict:
    out = {}
    for c in row.__table__.columns:
        v = getattr(row, c.key)
        out[c.key] = str(v) if isinstance(v, Decimal) else (v.isoformat() if hasattr(v, "isoformat") else v)
    return out


def export_all(s: Session, user_id: str) -> dict:
    data = {"exported_at": datetime.now(timezone.utc).isoformat(), "paper_trading_only": True}
    for name, model in [("portfolios", Portfolio), ("ledger", LedgerEntry), ("orders", Order), ("lots", PositionLot),
                        ("valuations", ValuationSnapshot), ("experiments", Experiment), ("proposals", Proposal),
                        ("approvals", Approval), ("strategy_versions", StrategyVersion), ("backtests", BacktestRun),
                        ("critiques", Critique), ("research_runs", ResearchRun), ("sources", Source), ("evidence", Evidence),
                        ("thesis_reviews", ThesisReview), ("lessons", Lesson), ("notifications", Notification),
                        ("audit", AuditEvent)]:
        rows = _rows(s, model, user_id)
        if name == "backtests":
            for r in rows:
                r.pop("series", None)
        data[name] = rows
    return data


def reset_lab(s: Session, user_id: str, actor: str) -> list[Portfolio]:
    """Archive (never erase) current portfolios/experiments and start a new tracked inception."""
    advisory_xact_lock(s, f"engine:{user_id}")
    ports = s.scalars(select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.status != "archived")).all()
    gen = max([p.generation for p in ports] + [0]) + 1
    for p in ports:
        p.status, p.paused_reason = "archived", "archived by explicit reset"
        for e in s.scalars(select(Experiment).where(Experiment.portfolio_id == p.id, Experiment.status.notin_(("closed", "completed")))).all():
            e.status, e.entries_allowed, e.status_reason = "closed", False, "archived by reset"
        for o in s.scalars(select(Order).where(Order.portfolio_id == p.id, Order.status == "pending")).all():
            o.status, o.status_reason, o.reserved_cash = "cancelled", "archived by reset", Decimal(0)
    for pr in s.scalars(select(Proposal).where(Proposal.user_id == user_id, Proposal.state == "pending")).all():
        pr.state, pr.state_reason = "withdrawn", "lab reset"
    new = init_portfolios(s, user_id, generation=gen)
    audit(s, user_id, actor, "lab.reset", "user", user_id, {"generation": gen - 1}, {"generation": gen})
    notify(s, user_id, "admin", "Lab reset — new inception", "Prior portfolios were archived (not deleted).", f"reset:{gen}", "/dashboard")
    return new
