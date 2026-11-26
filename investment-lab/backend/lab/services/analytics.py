"""Performance analytics from stored snapshots and fills. Undefined metrics are None, never zero."""
from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Experiment, Instrument, Order, Portfolio, PositionLot
from .engine import experiment_metrics
from .portfolios import portfolio_state, snapshots

MIN_OBS_FOR_RISK = 20


def series_metrics(navs: list[float], bench: list[float | None], start_value: float) -> dict:
    out = {"observations": len(navs), "total_return": None, "benchmark_return": None, "excess_return": None,
           "volatility": None, "sharpe": None, "max_drawdown": None, "insufficient_history": len(navs) < MIN_OBS_FOR_RISK}
    if not navs:
        return out
    out["total_return"] = navs[-1] / start_value - 1 if start_value else None
    b = [x for x in bench if x is not None]
    if b:
        out["benchmark_return"] = b[-1] / start_value - 1
        out["excess_return"] = out["total_return"] - out["benchmark_return"]
    peak, mdd = start_value, 0.0
    for v in navs:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    out["max_drawdown"] = mdd
    if len(navs) >= MIN_OBS_FOR_RISK:
        rets = [navs[i] / navs[i - 1] - 1 for i in range(1, len(navs)) if navs[i - 1]]
        m = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) if len(rets) > 1 else 0
        out["volatility"] = sd * math.sqrt(252) if sd else None
        out["sharpe"] = (m / sd * math.sqrt(252)) if sd else None
    return out


def portfolio_summary(s: Session, p: Portfolio, start: date | None = None, end: date | None = None) -> dict:
    st = portfolio_state(s, p, end)
    snaps = snapshots(s, p.id, start, end)
    base = float(p.starting_cash)
    if start and snaps:
        prev = snapshots(s, p.id, None, start)
        prev = [x for x in prev if x.session < snaps[0].session]
        if prev:
            base = float(prev[-1].nav)
    navs = [float(x.nav) for x in snaps]
    bench = [float(x.benchmark_nav) if x.benchmark_nav is not None else None for x in snaps]
    if start and snaps:
        prev_b = [x for x in snapshots(s, p.id, None, start) if x.session < snaps[0].session and x.benchmark_nav]
        bstart = float(prev_b[-1].benchmark_nav) if prev_b else base
        if bstart and base:
            bench = [None if v is None else v * base / bstart for v in bench]
    m = series_metrics(navs, bench, base)
    exps = s.scalars(select(Experiment).where(Experiment.portfolio_id == p.id)).all()
    closed = s.scalars(select(PositionLot).where(PositionLot.portfolio_id == p.id, PositionLot.status == "closed")).all()
    unreal = sum((x["unrealized_pnl"] for x in st["positions"]), Decimal(0))
    realized = sum((lt.realized_pnl + lt.dividends for lt in s.scalars(select(PositionLot).where(PositionLot.portfolio_id == p.id)).all()), Decimal(0))
    last = snaps[-1] if snaps else None
    return {
        "id": p.id, "family": p.family, "name": p.name, "status": p.status, "circuit_breaker": p.circuit_breaker,
        "paused_reason": p.paused_reason, "inception_at": p.inception_at.isoformat(), "starting_cash": float(p.starting_cash),
        "nav": float(st["nav"]), "cash": float(st["cash"]), "reserved_cash": float(st["reserved"]),
        "holdings_value": float(st["holdings_value"]), "realized_pnl": float(realized), "unrealized_pnl": float(unreal),
        "all_time_return": float(st["nav"] / p.starting_cash - 1),
        "daily_return": (navs[-1] / navs[-2] - 1) if len(navs) >= 2 else None,
        "drawdown": float(last.drawdown) if last else 0.0, "valuation_quality": st["quality"],
        "last_snapshot_session": last.session.isoformat() if last else None,
        "benchmark_nav": float(last.benchmark_nav) if last and last.benchmark_nav is not None else None,
        "benchmark_basis": "price-only (benchmark dividends not reinvested)",
        "experiments_active": sum(1 for e in exps if e.status == "active"), "experiments_total": len(exps),
        "closed_trades": len(closed), "positions": len(st["positions"]), "metrics": m,
        "exposure": float(st["holdings_value"] / st["nav"]) if st["nav"] else None,
    }


def portfolio_series(s: Session, p: Portfolio, start: date | None = None, end: date | None = None) -> list[dict]:
    return [{"session": x.session.isoformat(), "nav": float(x.nav), "benchmark_nav": float(x.benchmark_nav) if x.benchmark_nav is not None else None,
             "cash": float(x.cash), "holdings": float(x.holdings_value), "drawdown": float(x.drawdown), "quality": x.quality}
            for x in snapshots(s, p.id, start, end)]


def trades(s: Session, *, portfolio_id: str | None = None, experiment_id: str | None = None, limit: int = 500) -> list[dict]:
    q = select(Order).where(Order.status.in_(("filled", "pending", "expired", "blocked", "cancelled")))
    if portfolio_id:
        q = q.where(Order.portfolio_id == portfolio_id)
    if experiment_id:
        q = q.where(Order.experiment_id == experiment_id)
    out = []
    for o in s.scalars(q.order_by(Order.created_at.desc()).limit(limit)).all():
        inst = s.get(Instrument, o.instrument_id)
        out.append({"id": o.id, "experiment_id": o.experiment_id, "symbol": inst.symbol, "side": o.side, "quantity": float(o.quantity),
                    "status": o.status, "status_reason": o.status_reason, "reason": o.reason,
                    "signal_session": o.signal_session.isoformat(), "eligible_session": o.eligible_session.isoformat(),
                    "fill_session": o.fill_session.isoformat() if o.fill_session else None,
                    "fill_price": float(o.fill_price) if o.fill_price else None, "reference_price": float(o.reference_price) if o.reference_price else None,
                    "slippage_bps": float(o.slippage_bps), "slippage_amount": float(o.slippage_amount), "gross_amount": float(o.gross_amount) if o.gross_amount else None,
                    "fill_at": o.fill_at.isoformat() if o.fill_at else None, "processed_at": o.processed_at.isoformat() if o.processed_at else None,
                    "bar_observed_at": o.bar_observed_at.isoformat() if o.bar_observed_at else None, "recorded_late": o.recorded_late,
                    "requested_at": o.requested_at.isoformat()})
    return out


def closed_lots(s: Session, *, portfolio_id: str | None = None, experiment_id: str | None = None) -> list[dict]:
    q = select(PositionLot).where(PositionLot.status == "closed")
    if portfolio_id:
        q = q.where(PositionLot.portfolio_id == portfolio_id)
    if experiment_id:
        q = q.where(PositionLot.experiment_id == experiment_id)
    out = []
    for lt in s.scalars(q.order_by(PositionLot.closed_at.desc())).all():
        inst = s.get(Instrument, lt.instrument_id)
        basis = lt.original_quantity * lt.entry_price
        out.append({"lot_id": lt.id, "experiment_id": lt.experiment_id, "symbol": inst.symbol, "quantity": float(lt.original_quantity),
                    "entry_price": float(lt.entry_price), "exit_price": float(lt.exit_price) if lt.exit_price else None,
                    "opened_session": lt.opened_session.isoformat(), "closed_session": lt.closed_session.isoformat() if lt.closed_session else None,
                    "realized_pnl": float(lt.realized_pnl), "dividends": float(lt.dividends),
                    "return": float((lt.realized_pnl + lt.dividends) / basis) if basis else None, "exit_reason": lt.exit_reason})
    return out


def attribution(s: Session, p: Portfolio) -> dict:
    by_exp, by_ticker, by_hold = {}, {}, {"0-5": 0.0, "6-20": 0.0, "21-60": 0.0, "61+": 0.0}
    from .engine import sessions_held

    st = portfolio_state(s, p)
    open_unreal = {x["lot_id"]: float(x["unrealized_pnl"]) for x in st["positions"]}
    for lt in s.scalars(select(PositionLot).where(PositionLot.portfolio_id == p.id)).all():
        pnl = float(lt.realized_pnl + lt.dividends) + open_unreal.get(lt.id, 0.0)
        exp = s.get(Experiment, lt.experiment_id)
        sym = s.get(Instrument, lt.instrument_id).symbol
        by_exp[exp.name] = by_exp.get(exp.name, 0.0) + pnl
        by_ticker[sym] = by_ticker.get(sym, 0.0) + pnl
        if lt.closed_session:
            h = sessions_held(lt.opened_session, lt.closed_session)
            key = "0-5" if h <= 5 else "6-20" if h <= 20 else "21-60" if h <= 60 else "61+"
            by_hold[key] += pnl
    return {"by_experiment": by_exp, "by_ticker": by_ticker, "by_holding_period_closed": by_hold}


def experiment_summary(s: Session, e: Experiment) -> dict:
    m = experiment_metrics(s, e)
    return {"id": e.id, "name": e.name, "portfolio_id": e.portfolio_id, "status": e.status, "thesis_status": e.thesis_status,
            "budget": float(e.budget), "approved_at": e.approved_at.isoformat(), "ends_by": e.ends_by.isoformat() if e.ends_by else None,
            "entries_allowed": e.entries_allowed, "status_reason": e.status_reason,
            "closed_trades": m["closed_trades"], "open_positions": m["open_positions"], "total_pnl": float(m["total_pnl"]),
            "realized_pnl": float(m["realized_pnl"]), "unrealized_pnl": float(m["unrealized_pnl"]),
            "return_on_budget": m["return_on_budget"], "win_rate": m["win_rate"], "avg_trade_return": m["avg_trade_return"],
            "age_days": (date.today() - e.approved_at.date()).days, "insufficient_history": m["closed_trades"] < 10}
