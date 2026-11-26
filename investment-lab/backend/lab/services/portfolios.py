"""Portfolio initialisation, state derivation (from the ledger) and administrative controls."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import advisory_xact_lock
from ..models import (
    BenchmarkAccount,
    Experiment,
    Instrument,
    LedgerEntry,
    MarketBar,
    Order,
    Portfolio,
    PositionLot,
    ValuationSnapshot,
    utcnow,
)
from ..providers.market import get_market_provider
from .core import audit, current_policy, notify

FAMILIES = [
    ("contrarian", "Contrarian"),
    ("growth", "Growth"),
    ("asymmetric", "Asymmetric Bets"),
    ("event_driven", "Event-Driven"),
    ("ai_generated", "AI-Generated"),
]
STARTING_CASH = Decimal("10000")


def init_portfolios(s: Session, user_id: str, generation: int = 1) -> list[Portfolio]:
    """Create five independent $10,000 cash portfolios plus matched benchmark shadow accounts."""
    advisory_xact_lock(s, f"init:{user_id}")
    existing = s.scalars(select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.generation == generation)).all()
    if existing:
        return list(existing)
    pol = current_policy(s, user_id)
    bench = get_market_provider().benchmark()
    out = []
    now = utcnow()
    for fam, name in FAMILIES:
        p = Portfolio(user_id=user_id, family=fam, name=name, generation=generation, inception_at=now,
                      starting_cash=STARTING_CASH, peak_nav=STARTING_CASH, policy_version=pol.version)
        s.add(p)
        s.flush()
        s.add(LedgerEntry(user_id=user_id, portfolio_id=p.id, event_type="initial_cash", amount=STARTING_CASH,
                          idempotency_key=f"init:{p.id}", occurred_at=now, memo="Initial virtual capital (paper)"))
        s.add(BenchmarkAccount(user_id=user_id, portfolio_id=p.id, symbol=bench, starting_cash=STARTING_CASH,
                               inception_at=now, cash=STARTING_CASH, shares=Decimal(0)))
        audit(s, user_id, "system", "portfolio.create", "portfolio", p.id, None, {"family": fam, "cash": "10000"})
        out.append(p)
    s.flush()
    return out


def active_portfolios(s: Session, user_id: str) -> list[Portfolio]:
    return list(s.scalars(select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.status != "archived")
                          .order_by(Portfolio.created_at)).all())


def cash_balance(s: Session, portfolio_id: str) -> Decimal:
    return s.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(LedgerEntry.portfolio_id == portfolio_id)) or Decimal(0)


def reserved_cash(s: Session, portfolio_id: str) -> Decimal:
    return s.scalar(select(func.coalesce(func.sum(Order.reserved_cash), 0)).where(
        Order.portfolio_id == portfolio_id, Order.status == "pending", Order.side == "buy")) or Decimal(0)


def open_lots(s: Session, portfolio_id: str, experiment_id: str | None = None) -> list[PositionLot]:
    q = select(PositionLot).where(PositionLot.portfolio_id == portfolio_id, PositionLot.status.in_(("open", "closing", "unresolved")))
    if experiment_id:
        q = q.where(PositionLot.experiment_id == experiment_id)
    return list(s.scalars(q).all())


def last_close(s: Session, instrument_id: str, asof: date | None = None) -> MarketBar | None:
    q = select(MarketBar).where(MarketBar.instrument_id == instrument_id)
    if asof:
        q = q.where(MarketBar.session <= asof)
    return s.scalars(q.order_by(MarketBar.session.desc()).limit(1)).first()


def portfolio_state(s: Session, p: Portfolio, asof: date | None = None) -> dict:
    cash = cash_balance(s, p.id)
    reserved = reserved_cash(s, p.id)
    holdings = Decimal(0)
    positions = []
    stale = False
    for lot in open_lots(s, p.id):
        bar = last_close(s, lot.instrument_id, asof)
        inst = s.get(Instrument, lot.instrument_id)
        px = bar.close if bar else lot.entry_price
        val = lot.quantity * px
        holdings += val
        unresolved = lot.status == "unresolved" or inst.status != "active"
        stale = stale or unresolved or bar is None
        positions.append({"lot_id": lot.id, "symbol": inst.symbol, "experiment_id": lot.experiment_id, "quantity": lot.quantity,
                          "entry_price": lot.entry_price, "cost_basis": lot.cost_basis, "last_price": px,
                          "price_session": bar.session if bar else None, "market_value": val,
                          "unrealized_pnl": val - lot.cost_basis, "status": lot.status, "opened_session": lot.opened_session,
                          "unresolved_valuation": unresolved, "sector": inst.sector, "type": inst.type})
    nav = cash + holdings
    return {"cash": cash, "reserved": reserved, "holdings_value": holdings, "nav": nav, "positions": positions,
            "quality": "unresolved" if stale else "ok"}


def realized_pnl(s: Session, portfolio_id: str, experiment_id: str | None = None) -> Decimal:
    q = select(func.coalesce(func.sum(PositionLot.realized_pnl + PositionLot.dividends), 0)).where(PositionLot.portfolio_id == portfolio_id)
    if experiment_id:
        q = q.where(PositionLot.experiment_id == experiment_id)
    return s.scalar(q) or Decimal(0)


def reconcile(s: Session, p: Portfolio) -> dict:
    """Ledger-derived cash and lot quantities must agree with stored lots; returns discrepancies."""
    issues = []
    lots = s.scalars(select(PositionLot).where(PositionLot.portfolio_id == p.id)).all()
    for lot in lots:
        qty_ledger = s.scalar(select(func.coalesce(func.sum(LedgerEntry.quantity_delta), 0))
                              .where(LedgerEntry.lot_id == lot.id)) or Decimal(0)
        if qty_ledger != lot.quantity:
            issues.append({"lot": lot.id, "ledger_qty": str(qty_ledger), "lot_qty": str(lot.quantity)})
    cash = cash_balance(s, p.id)
    if cash < 0:
        issues.append({"negative_cash": str(cash)})
    # P&L identity: NAV - starting cash == realized + unrealized (+ dividends)
    st = portfolio_state(s, p)
    unreal = sum((x["unrealized_pnl"] for x in st["positions"]), Decimal(0))
    real = realized_pnl(s, p.id)
    open_div = s.scalar(select(func.coalesce(func.sum(PositionLot.dividends), 0)).where(
        PositionLot.portfolio_id == p.id, PositionLot.status != "closed")) or Decimal(0)
    lhs = st["nav"] - p.starting_cash
    rhs = real + unreal
    if abs(lhs - rhs) > Decimal("0.01"):
        issues.append({"pnl_identity": {"nav_minus_start": str(lhs), "realized_plus_unrealized": str(rhs),
                                        "open_lot_dividends": str(open_div)}})
    return {"ok": not issues, "issues": issues}


def set_pause(s: Session, user_id: str, p: Portfolio, paused: bool, actor: str, reason: str = "") -> Portfolio:
    advisory_xact_lock(s, f"engine:{user_id}")
    prior = {"status": p.status, "circuit_breaker": p.circuit_breaker}
    if paused:
        p.status = "paused"
        p.paused_reason = reason or "paused by user"
    else:
        p.status = "active"
        p.paused_reason = None
        if p.circuit_breaker:
            p.circuit_breaker = False  # explicit approval to resume after a drawdown breaker
            st = portfolio_state(s, p)
            p.peak_nav = max(st["nav"], Decimal(0))
    audit(s, user_id, actor, "portfolio.resume" if not paused else "portfolio.pause", "portfolio", p.id, prior,
          {"status": p.status, "circuit_breaker": p.circuit_breaker}, {"reason": reason})
    notify(s, user_id, "portfolio", f"{p.name} {'paused' if paused else 'resumed'}",
           "New entries are paused; approved exits continue." if paused else "New entries may resume under approved rules.",
           f"pause:{p.id}:{utcnow().isoformat()}", f"/portfolios/{p.id}")
    return p


def close_experiment(s: Session, user_id: str, exp: Experiment, actor: str) -> Experiment:
    """User-confirmed close: stop entries, sell all lots at the next eligible open (no forced past fills)."""
    advisory_xact_lock(s, f"engine:{user_id}")
    prior = {"status": exp.status}
    exp.entries_allowed = False
    exp.status = "closing"
    exp.status_reason = "closed by user (explicit confirmation)"
    for o in s.scalars(select(Order).where(Order.experiment_id == exp.id, Order.status == "pending", Order.side == "buy")).all():
        o.status = "cancelled"
        o.status_reason = "experiment closed by user"
        o.reserved_cash = Decimal(0)
    audit(s, user_id, actor, "experiment.close", "experiment", exp.id, prior, {"status": exp.status})
    return exp


def snapshots(s: Session, portfolio_id: str, start: date | None = None, end: date | None = None) -> list[ValuationSnapshot]:
    q = select(ValuationSnapshot).where(ValuationSnapshot.portfolio_id == portfolio_id)
    if start:
        q = q.where(ValuationSnapshot.session >= start)
    if end:
        q = q.where(ValuationSnapshot.session <= end)
    return list(s.scalars(q.order_by(ValuationSnapshot.session)).all())


def benchmark_for(s: Session, portfolio_id: str) -> BenchmarkAccount | None:
    return s.scalars(select(BenchmarkAccount).where(BenchmarkAccount.portfolio_id == portfolio_id)).first()
