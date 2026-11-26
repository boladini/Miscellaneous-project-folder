"""Deterministic forward paper-trading engine (no LLM involvement).

Daily cycle for finalized session T, executed inside one transaction under a per-user advisory lock:
  1. corporate actions effective T            4. exit signals on close T  -> sell at next eligible open
  2. fill eligible pending orders at open T    5. entry signals on close T -> risk check -> buy intents
  3. mark-to-close T, benchmark, snapshot,     6. monitoring: thesis status, invalidation rules, alerts
     drawdown circuit breaker
Signals are only generated for the latest finalized session (catch-up runs fill previously queued
orders at recorded opens but never create retroactive signals).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .. import market_calendar as cal
from ..config import get_settings
from ..db import advisory_xact_lock
from ..execution import D, fill_price, q6, reservation_for, whole_shares
from ..indicators import BarView, PositionContext, compute
from ..models import (
    BenchmarkAccount,
    CorporateAction,
    Experiment,
    Instrument,
    LedgerEntry,
    MarketBar,
    Order,
    Portfolio,
    PositionLot,
    Signal,
    StrategyVersion,
    ThesisReview,
    ValuationSnapshot,
    utcnow,
)
from ..risk import EntryContext, check_entry
from ..rules import evaluate, first_true_reason, has_missing
from ..strategy_schema import Indicator, StrategySpec
from .core import audit, get_user_settings, notify, policy_by_version
from .market_data import bars_for, events_for, namespace, to_bar
from .portfolios import cash_balance, open_lots, portfolio_state, reserved_cash

EXPERIMENT_LIFETIME_SESSIONS = 252


def _bar(s: Session, instrument_id: str, d: date) -> MarketBar | None:
    return s.scalars(select(MarketBar).where(MarketBar.instrument_id == instrument_id, MarketBar.session == d)).first()


def _spec(s: Session, version_id: str) -> StrategySpec:
    return StrategySpec.model_validate(s.get(StrategyVersion, version_id).rule_json)


def sessions_held(opened: date, t: date) -> int:
    return len(cal.sessions_between(cal.next_session(opened), t)) if t > opened else 0


def run_daily(s: Session, user_id: str, t: date, now: datetime | None = None, generate_signals: bool | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    cfg = get_settings()
    if not cal.is_session(t):
        return {"session": t.isoformat(), "skipped": "not a trading session"}
    latest = cal.latest_finalized_session(now, cfg.daily_data_ready_hour_et, cfg.daily_data_ready_minute_et)
    if t > latest:
        return {"session": t.isoformat(), "skipped": "session data not yet finalized"}
    catch_up = t < latest
    if generate_signals is None:
        generate_signals = not catch_up
    advisory_xact_lock(s, f"engine:{user_id}")
    settings = get_user_settings(s, user_id)
    summary = {"session": t.isoformat(), "catch_up": catch_up, "fills": 0, "expired": 0, "exits": 0, "entries": 0,
               "blocked": 0, "portfolios": {}, "signals_generated": generate_signals}
    late = now > cal.data_ready_utc(cal.next_session(t))
    for p in s.scalars(select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.status != "archived")).all():
        ps = {"fills": 0, "exits": 0, "entries": 0, "blocked": [], "notes": []}
        _corporate_actions(s, p, t)
        _fill_orders(s, p, t, now, late, ps, summary)
        snap = _mark(s, p, t, ps)
        if generate_signals:
            _exit_signals(s, p, t, now, ps)
            _entry_signals(s, p, t, now, settings, ps)
            _monitor(s, p, t, snap)
        summary["fills"] += ps["fills"]
        summary["exits"] += ps["exits"]
        summary["entries"] += ps["entries"]
        summary["blocked"] += len(ps["blocked"])
        summary["portfolios"][p.family] = {k: ps[k] for k in ("fills", "exits", "entries")} | {
            "nav": str(snap.nav), "blocked": ps["blocked"][:10], "notes": ps["notes"][:10]}
    audit(s, user_id, "worker", "engine.daily", "session", t.isoformat(), None, summary)
    return summary


# ---------------------------------------------------------------------------------------------
def _corporate_actions(s: Session, p: Portfolio, t: date) -> None:
    lots = open_lots(s, p.id)
    if not lots:
        return
    for lot in lots:
        acts = s.scalars(select(CorporateAction).where(CorporateAction.instrument_id == lot.instrument_id,
                                                       CorporateAction.ex_date == t)).all()
        for a in acts:
            key = f"ca:{a.id}:{lot.id}"
            if s.scalar(select(LedgerEntry.id).where(LedgerEntry.idempotency_key == key)):
                continue
            first_bar = s.scalar(select(MarketBar.adjustment_basis).where(MarketBar.instrument_id == lot.instrument_id).limit(1))
            if a.type == "split" and a.ratio and first_bar == "raw" and lot.opened_session < t:
                r = D(a.ratio)
                new_qty = lot.quantity * r
                delta = new_qty - lot.quantity
                lot.quantity = new_qty
                lot.original_quantity = lot.original_quantity * r
                lot.entry_price = q6(lot.entry_price / r)
                if lot.peak_close:
                    lot.peak_close = q6(lot.peak_close / r)
                s.add(LedgerEntry(user_id=p.user_id, portfolio_id=p.id, experiment_id=lot.experiment_id,
                                  instrument_id=lot.instrument_id, lot_id=lot.id, event_type="split", amount=Decimal(0),
                                  quantity_delta=delta, reference=a.id, idempotency_key=key, occurred_at=utcnow(), session=t,
                                  memo=f"{a.ratio}:1 split; basis unchanged"))
                for o in s.scalars(select(Order).where(Order.lot_id == lot.id, Order.status == "pending")).all():
                    o.quantity = lot.quantity
            elif a.type == "dividend" and a.amount and first_bar == "raw" and lot.opened_session < t:
                amt = q6(lot.quantity * D(a.amount))
                lot.dividends = lot.dividends + amt
                s.add(LedgerEntry(user_id=p.user_id, portfolio_id=p.id, experiment_id=lot.experiment_id,
                                  instrument_id=lot.instrument_id, lot_id=lot.id, event_type="dividend", amount=amt,
                                  reference=a.id, idempotency_key=key, occurred_at=utcnow(), session=t,
                                  memo=f"cash dividend {a.amount}/sh credited at ex-date (simulation convention)"))
            elif a.type in ("merger", "symbol_change") or a.needs_manual_reconciliation:
                notify(s, p.user_id, "reconciliation", "Manual reconciliation required",
                       f"Corporate action '{a.type}' on {t} affects a held position; fills for it are suspended.",
                       f"recon:{a.id}:{lot.id}", f"/portfolios/{p.id}", "urgent")
                lot.status = "unresolved"
            elif a.type == "delisting":
                lot.status = "unresolved"
                notify(s, p.user_id, "reconciliation", "Held instrument delisted — valuation unresolved",
                       "No liquidation price is invented. The position is marked at its last recorded close and "
                       "flagged unresolved until reconciled.", f"delist:{a.id}:{lot.id}", f"/portfolios/{p.id}", "urgent")


def _fill_orders(s: Session, p: Portfolio, t: date, now: datetime, late: bool, ps: dict, summary: dict) -> None:
    orders = s.scalars(select(Order).where(Order.portfolio_id == p.id, Order.status == "pending",
                                           Order.eligible_session <= t).order_by(Order.side.desc(), Order.created_at)
                       .with_for_update()).all()  # sells before buys frees cash first
    for o in orders:
        inst = s.get(Instrument, o.instrument_id)
        bar = _bar(s, o.instrument_id, t)
        lot = s.get(PositionLot, o.lot_id) if o.lot_id else None
        if bar is None or inst.status != "active" or (lot is not None and lot.status == "unresolved"):
            if t >= o.expires_after_session:
                o.status = "expired"
                o.status_reason = f"no eligible market data through {o.expires_after_session} (no data means no fill)"
                o.reserved_cash = Decimal(0)
                o.processed_at = now
                summary["expired"] += 1
                notify(s, p.user_id, "order", f"Order expired: {o.side} {inst.symbol}", o.status_reason,
                       f"expire:{o.id}", f"/experiments/{o.experiment_id}", "digest", data_timestamp=now)
            else:
                o.status_reason = f"waiting: no tradable bar for {inst.symbol} on {t}"
            continue
        px = fill_price(bar.open, o.side, o.slippage_bps)
        open_ts = cal.session_open_utc(t)
        if o.side == "buy":
            cash = cash_balance(s, p.id)
            others = reserved_cash(s, p.id) - o.reserved_cash
            available = cash - others
            qty = o.quantity
            cost = q6(qty * px)
            if cost > available:
                qty = whole_shares(available, px)
                cost = q6(qty * px)
            if qty <= 0:
                o.status, o.status_reason, o.reserved_cash, o.processed_at = "blocked", "insufficient cash at fill", Decimal(0), now
                ps["blocked"].append(f"{inst.symbol}: insufficient cash at fill")
                continue
            o.quantity = qty
            o.fill_price, o.fill_session, o.fill_at, o.bar_observed_at = px, t, open_ts, bar.observed_at
            o.slippage_amount = q6(qty * (px - bar.open))
            o.gross_amount = cost
            o.status, o.reserved_cash, o.processed_at, o.recorded_late = "filled", Decimal(0), now, late
            exp = s.get(Experiment, o.experiment_id)
            new_lot = PositionLot(user_id=p.user_id, portfolio_id=p.id, experiment_id=o.experiment_id,
                                  strategy_version_id=exp.approved_version_id, instrument_id=o.instrument_id,
                                  open_order_id=o.id, quantity=qty, original_quantity=qty, cost_basis=cost, entry_price=px,
                                  opened_at=open_ts, opened_session=t, peak_close=bar.close)
            s.add(new_lot)
            s.flush()
            s.add(LedgerEntry(user_id=p.user_id, portfolio_id=p.id, experiment_id=o.experiment_id, instrument_id=o.instrument_id,
                              lot_id=new_lot.id, event_type="buy", amount=-cost, quantity_delta=qty, reference=o.id,
                              idempotency_key=f"fill:{o.id}", occurred_at=open_ts, session=t,
                              memo=f"BUY {qty} {inst.symbol} @ {px} (open {bar.open} + {o.slippage_bps} bps)"))
            if exp.thesis_status == "untested":
                exp.thesis_status = "active"
            msg = f"Simulated BUY {qty} {inst.symbol} at {px} (session {t} open + slippage)."
        else:
            if lot is None or lot.quantity <= 0:
                o.status, o.status_reason, o.processed_at = "cancelled", "lot already closed", now
                continue
            qty = min(o.quantity, lot.quantity)
            proceeds = q6(qty * px)
            basis_part = q6(lot.cost_basis * qty / lot.quantity)
            pnl = proceeds - basis_part
            o.quantity = qty
            o.fill_price, o.fill_session, o.fill_at, o.bar_observed_at = px, t, open_ts, bar.observed_at
            o.slippage_amount = q6(qty * (bar.open - px))
            o.gross_amount = proceeds
            o.status, o.processed_at, o.recorded_late = "filled", now, late
            lot.quantity -= qty
            lot.cost_basis -= basis_part
            lot.realized_pnl += pnl
            if lot.quantity == 0:
                lot.status, lot.closed_at, lot.closed_session, lot.exit_price = "closed", open_ts, t, px
            else:
                lot.status = "open"
            s.add(LedgerEntry(user_id=p.user_id, portfolio_id=p.id, experiment_id=o.experiment_id, instrument_id=o.instrument_id,
                              lot_id=lot.id, event_type="sell", amount=proceeds, quantity_delta=-qty, reference=o.id,
                              idempotency_key=f"fill:{o.id}", occurred_at=open_ts, session=t,
                              memo=f"SELL {qty} {inst.symbol} @ {px}; realized {pnl}"))
            msg = f"Simulated SELL {qty} {inst.symbol} at {px}; realized P&L {pnl:+.2f}. Reason: {o.reason}"
        ps["fills"] += 1
        notify(s, p.user_id, "fill", f"{p.name}: {o.side.upper()} {inst.symbol}", msg + (" (recorded late; fill uses the "
               "recorded open of the eligible session)" if late else ""), f"fill:{o.id}", f"/experiments/{o.experiment_id}",
               "digest", data_timestamp=bar.observed_at)


def _mark(s: Session, p: Portfolio, t: date, ps: dict) -> ValuationSnapshot:
    pol_age = 5
    for lot in open_lots(s, p.id):
        bar = _bar(s, lot.instrument_id, t)
        if bar is not None:
            lot.peak_close = max(lot.peak_close or bar.close, bar.close)
        else:
            inst = s.get(Instrument, lot.instrument_id)
            rows = bars_for(s, lot.instrument_id, end=t)
            last = rows[-1].session if rows else None
            age = len(cal.sessions_between(cal.next_session(last), t)) if last else 999
            if age >= pol_age or inst.status != "active":
                if lot.status != "unresolved":
                    lot.status = "unresolved"
                    notify(s, p.user_id, "stale_data", f"{inst.symbol}: no market data for {age} sessions",
                           "Fills suspended; valuation uses the last recorded close and is flagged unresolved. "
                           "No liquidation price is invented.", f"unresolved:{lot.id}", f"/portfolios/{p.id}", "urgent")
            ps["notes"].append(f"{inst.symbol}: no bar for {t}")
    st = portfolio_state(s, p, t)
    bench = s.scalars(select(BenchmarkAccount).where(BenchmarkAccount.portfolio_id == p.id)).first()
    bnav = None
    if bench:
        binst = s.scalars(select(Instrument).where(Instrument.namespace == namespace(), Instrument.symbol == bench.symbol)).first()
        if binst:
            if bench.entry_session is None and t >= cal.first_open_after(bench.inception_at):
                bbar = _bar(s, binst.id, t)
                if bbar:
                    bpx = fill_price(bbar.open, "buy", Decimal("10"))
                    bench.shares = (bench.cash / bpx).quantize(Decimal("0.0000000001"))
                    bench.cash = bench.cash - q6(bench.shares * bpx)
                    bench.entry_session, bench.entry_price = t, bpx
            if bench.entry_session is not None:
                lb = _bar(s, binst.id, t) or s.scalars(select(MarketBar).where(MarketBar.instrument_id == binst.id,
                                                                               MarketBar.session <= t)
                                                       .order_by(MarketBar.session.desc()).limit(1)).first()
                if lb:
                    bnav = q6(bench.cash + bench.shares * lb.close)
            else:
                bnav = bench.cash
    peak = max(p.peak_nav, st["nav"])
    p.peak_nav = peak
    dd = (st["nav"] / peak - 1) if peak > 0 else Decimal(0)
    details = {"positions": len(st["positions"]), "basis": bench.basis if bench else None}
    values = dict(user_id=p.user_id, portfolio_id=p.id, session=t, cash=st["cash"], reserved_cash=st["reserved"],
                  holdings_value=st["holdings_value"], nav=st["nav"], benchmark_nav=bnav, peak_nav=peak,
                  drawdown=dd.quantize(Decimal("0.00000001")), quality=st["quality"], details=details, created_at=utcnow())
    stmt = pg_insert(ValuationSnapshot).values(**values)
    stmt = stmt.on_conflict_do_update(index_elements=["portfolio_id", "session"],
                                      set_={k: stmt.excluded[k] for k in ("cash", "reserved_cash", "holdings_value", "nav",
                                                                           "benchmark_nav", "peak_nav", "drawdown", "quality",
                                                                           "details")})
    s.execute(stmt)
    limit = Decimal(str(_limits(s, p).get("drawdown_circuit_breaker", 0.15)))
    if dd <= -limit and not p.circuit_breaker:
        p.circuit_breaker = True
        audit(s, p.user_id, "risk_engine", "portfolio.circuit_breaker", "portfolio", p.id, None, {"drawdown": str(dd)})
        notify(s, p.user_id, "drawdown", f"{p.name}: drawdown circuit breaker tripped ({float(dd):.1%})",
               "New entries are paused; approved exits continue. Resume requires your explicit approval.",
               f"cb:{p.id}:{t}", f"/portfolios/{p.id}", "urgent", data_timestamp=cal.session_close_utc(t))
    return s.scalars(select(ValuationSnapshot).where(ValuationSnapshot.portfolio_id == p.id, ValuationSnapshot.session == t)).one()


def _limits(s: Session, p: Portfolio) -> dict:
    pol = policy_by_version(s, p.user_id, p.policy_version)
    return pol.limits if pol else {}


def _view(s: Session, instrument_id: str, t: date, decision_time: datetime, cache: dict) -> BarView | None:
    if instrument_id not in cache:
        rows = bars_for(s, instrument_id, end=t)
        evs = [e for e in events_for(s, instrument_id) if e.known_at <= decision_time]
        cache[instrument_id] = BarView([to_bar(r) for r in rows], t, decision_time, evs) if rows else None
    return cache[instrument_id]


def _exit_signals(s: Session, p: Portfolio, t: date, now: datetime, ps: dict) -> None:
    decision_time = cal.data_ready_utc(t)
    cache: dict = {}
    nxt = cal.next_session(t)
    for lot in open_lots(s, p.id):
        if lot.status in ("closing", "unresolved"):
            continue
        exp = s.get(Experiment, lot.experiment_id)
        spec = _spec(s, lot.strategy_version_id)  # legacy lots keep their original approved rules
        view = _view(s, lot.instrument_id, t, decision_time, cache)
        if view is None or view.bars[-1].session != t:
            continue
        held = sessions_held(lot.opened_session, t)
        if exp.status == "closing":
            hit, trace, reason = True, {"forced": "experiment closed by user"}, "experiment closed by user (confirmed)"
        else:
            if held < spec.holding.min_sessions:
                continue
            pos = PositionContext(float(lot.entry_price), lot.opened_session, held, float(lot.peak_close or lot.entry_price))
            hit, trace = evaluate(spec.exit, view, pos)
            reason = first_true_reason(trace)
            if held >= spec.holding.max_sessions:
                hit, reason = True, f"max holding {spec.holding.max_sessions} sessions reached"
        if not hit:
            continue
        key = f"exit:{lot.id}"
        s.execute(pg_insert(Signal).values(id=__import__("uuid").uuid4().hex, experiment_id=exp.id, instrument_id=lot.instrument_id,
                                           session=t, kind="exit", observed_at=cal.session_close_utc(t), rules_evaluated=trace,
                                           result=True, idempotency_key=f"{key}:{t}", created_at=utcnow())
                  .on_conflict_do_nothing(index_elements=["idempotency_key"]))
        res = s.execute(pg_insert(Order).values(
            id=__import__("uuid").uuid4().hex, user_id=p.user_id, portfolio_id=p.id, experiment_id=exp.id,
            instrument_id=lot.instrument_id, lot_id=lot.id, side="sell", quantity=lot.quantity, status="pending",
            reason=reason or "exit rule", signal_session=t, eligible_session=nxt,
            expires_after_session=cal.add_sessions(t, spec.execution.order_expiry_sessions), reserved_cash=Decimal(0),
            reference_price=view.bars[-1].close, slippage_bps=D(spec.execution.slippage_bps), fees=Decimal(0),
            slippage_amount=Decimal(0), recorded_late=False, idempotency_key=f"sell:{lot.id}", requested_at=now, created_at=utcnow(),
        ).on_conflict_do_nothing(index_elements=["idempotency_key"]).returning(Order.id)).first()
        if res is not None:
            lot.status = "closing"
            ps["exits"] += 1
            inst = s.get(Instrument, lot.instrument_id)
            notify(s, p.user_id, "exit", f"{p.name}: exit signal {inst.symbol}",
                   f"Exit rule triggered on {t} close ({reason}). Simulated sell at the next eligible open ({nxt}); "
                   "the fill price is not guaranteed and may gap.", f"exitsig:{lot.id}", f"/experiments/{exp.id}", "digest",
                   data_timestamp=cal.session_close_utc(t))


def _entry_signals(s: Session, p: Portfolio, t: date, now: datetime, settings: dict, ps: dict) -> None:
    exps = s.scalars(select(Experiment).where(Experiment.portfolio_id == p.id, Experiment.status == "active",
                                              Experiment.entries_allowed.is_(True))).all()
    if not exps:
        return
    global_pause = bool(settings.get("global_pause"))
    limits = _limits(s, p)
    decision_time = cal.data_ready_utc(t)
    insts = s.scalars(select(Instrument).where(Instrument.namespace == namespace(), Instrument.status == "active")).all()
    cache: dict = {}
    nxt = cal.next_session(t)
    for exp in exps:
        if exp.ends_by and t >= exp.ends_by:
            exp.entries_allowed = False
            exp.status_reason = "experiment authorization period ended; managing exits only"
            continue
        # Signals must use a close that happened AFTER approval: never backdate.
        if t < cal.first_session_with_close_after(exp.approved_at):
            ps["notes"].append(f"{exp.name}: approved after {t} close; first eligible signal later")
            continue
        version = s.get(StrategyVersion, exp.approved_version_id)
        spec = StrategySpec.model_validate(version.rule_json)
        lots = open_lots(s, p.id)
        my_lots = [lt for lt in lots if lt.experiment_id == exp.id]
        pend = s.scalars(select(Order).where(Order.portfolio_id == p.id, Order.status == "pending")).all()
        my_pending_buys = [o for o in pend if o.experiment_id == exp.id and o.side == "buy"]
        slots = spec.allocation.max_positions - len(my_lots) - len(my_pending_buys)
        if slots <= 0:
            continue
        held_ids = {lt.instrument_id for lt in my_lots} | {o.instrument_id for o in my_pending_buys}
        cands = []
        evaluated = 0
        for inst in insts:
            if inst.type not in spec.universe.asset_types or inst.id in held_ids:
                continue
            if spec.universe.symbols and inst.symbol not in spec.universe.symbols:
                continue
            if inst.symbol in spec.universe.exclude_symbols:
                continue
            view = _view(s, inst.id, t, decision_time, cache)
            if view is None or view.bars[-1].session != t:
                continue
            evaluated += 1
            ok, trace = evaluate(spec.entry, view)
            if not ok or has_missing(trace):
                continue
            rv = compute(spec.ranking.indicator, spec.ranking.params, view)
            if rv is None:
                continue
            cands.append((rv, inst, view, trace))
        cands.sort(key=lambda x: x[0], reverse=(spec.ranking.direction == "descending"))
        for rv, inst, view, trace in cands[:slots]:
            st = portfolio_state(s, p, t)
            pend = s.scalars(select(Order).where(Order.portfolio_id == p.id, Order.status == "pending", Order.side == "buy")).all()
            tick_exp = sum((x["market_value"] for x in st["positions"] if x["symbol"] == inst.symbol), Decimal(0)) + \
                sum((o.reserved_cash for o in pend if o.instrument_id == inst.id), Decimal(0))
            sector_exp = None
            if inst.sector:
                sector_exp = sum((x["market_value"] for x in st["positions"] if x["sector"] == inst.sector), Decimal(0)) + \
                    sum((o.reserved_cash for o in pend if s.get(Instrument, o.instrument_id).sector == inst.sector), Decimal(0))
            committed = sum((lt.cost_basis for lt in open_lots(s, p.id, exp.id)), Decimal(0)) + \
                sum((o.reserved_cash for o in pend if o.experiment_id == exp.id), Decimal(0))
            price = view.bars[-1].close
            ctx = EntryContext(
                nav=st["nav"], cash=st["cash"], reserved=st["reserved"], ticker_exposure=tick_exp, sector_exposure=sector_exp,
                experiment_committed=committed, experiment_budget=exp.budget, instrument_type=inst.type, price=price,
                avg_dollar_volume_20d=compute(Indicator.avg_dollar_volume, {"n": 20}, view), bar_age_sessions=0,
                portfolio_paused=p.status == "paused", circuit_breaker=p.circuit_breaker, experiment_entries_allowed=exp.entries_allowed,
                global_paused=global_pause, binary_catalyst=spec.catalyst.binary,
                strategy_max_position_fraction=spec.allocation.max_position_fraction,
                strategy_max_experiment_fraction=spec.allocation.max_experiment_fraction,
                strategy_min_price=spec.universe.min_price, strategy_min_adv=spec.universe.min_avg_dollar_volume_20d)
            dec = check_entry(ctx, limits)
            sig_key = f"entry:{exp.id}:{inst.id}:{t}"
            s.execute(pg_insert(Signal).values(id=__import__("uuid").uuid4().hex, experiment_id=exp.id, instrument_id=inst.id,
                                               session=t, kind="entry", observed_at=cal.session_close_utc(t),
                                               rules_evaluated={"trace": trace, "rank_value": rv, "risk": {
                                                   "allowed": dec.allowed, "reasons": dec.reasons, "binding": dec.binding_limit}},
                                               result=dec.allowed, idempotency_key=sig_key, created_at=utcnow())
                      .on_conflict_do_nothing(index_elements=["idempotency_key"]))
            if not dec.allowed:
                ps["blocked"].append(f"{inst.symbol}: {'; '.join(dec.reasons)}")
                continue
            slip = D(spec.execution.slippage_bps)
            est = D(price) * (1 + (slip + Decimal(300)) / Decimal(10000))
            qty = whole_shares(dec.max_notional, est, spec.execution.fractional_shares)
            if qty <= 0:
                ps["blocked"].append(f"{inst.symbol}: position size rounds to 0 whole shares")
                continue
            res = s.execute(pg_insert(Order).values(
                id=__import__("uuid").uuid4().hex, user_id=p.user_id, portfolio_id=p.id, experiment_id=exp.id,
                instrument_id=inst.id, side="buy", quantity=qty, status="pending",
                reason=f"entry rules met on {t} close (rank {spec.ranking.indicator.value}={rv:.4f})", signal_session=t,
                eligible_session=nxt, expires_after_session=cal.add_sessions(t, spec.execution.order_expiry_sessions),
                reserved_cash=reservation_for(qty, D(price), slip), reference_price=D(price), slippage_bps=slip,
                fees=Decimal(0), slippage_amount=Decimal(0), recorded_late=False, idempotency_key=f"buy:{sig_key}",
                requested_at=now, created_at=utcnow(),
            ).on_conflict_do_nothing(index_elements=["idempotency_key"]).returning(Order.id)).first()
            if res is not None:
                ps["entries"] += 1
                notify(s, p.user_id, "order", f"{p.name}: entry signal {inst.symbol}",
                       f"Simulated buy of {qty} {inst.symbol} queued for the {nxt} open (approved rules met on {t} close).",
                       f"order:{sig_key}", f"/experiments/{exp.id}", "digest", data_timestamp=cal.session_close_utc(t))
        ps["notes"].append(f"{exp.name}: evaluated {evaluated} instruments, {len(cands)} met entry rules")


def experiment_metrics(s: Session, exp: Experiment, asof: date | None = None) -> dict:
    lots = s.scalars(select(PositionLot).where(PositionLot.experiment_id == exp.id)).all()
    closed = [lt for lt in lots if lt.status == "closed"]
    realized = sum((lt.realized_pnl + lt.dividends for lt in lots), Decimal(0))
    unreal = Decimal(0)
    open_value = Decimal(0)
    for lt in lots:
        if lt.status != "closed":
            bar = s.scalars(select(MarketBar).where(MarketBar.instrument_id == lt.instrument_id, *(
                [MarketBar.session <= asof] if asof else [])).order_by(MarketBar.session.desc()).limit(1)).first()
            px = bar.close if bar else lt.entry_price
            open_value += lt.quantity * px
            unreal += lt.quantity * px - lt.cost_basis
    rets = [float((lt.realized_pnl + lt.dividends) / (lt.original_quantity * lt.entry_price)) for lt in closed
            if lt.original_quantity and lt.entry_price]
    wins = [r for r in rets if r > 0]
    total = realized + unreal
    return {"closed_trades": len(closed), "open_positions": len([lt for lt in lots if lt.status != "closed"]),
            "realized_pnl": realized, "unrealized_pnl": unreal, "total_pnl": total,
            "return_on_budget": float(total / exp.budget) if exp.budget else None,
            "win_rate": len(wins) / len(rets) if rets else None,
            "avg_trade_return": sum(rets) / len(rets) if rets else None, "open_value": open_value}


def _monitor(s: Session, p: Portfolio, t: date, snap: ValuationSnapshot) -> None:
    """Monitoring agent (deterministic): thesis status, pre-approved invalidation rules, completion."""
    exps = s.scalars(select(Experiment).where(Experiment.portfolio_id == p.id,
                                              Experiment.status.in_(("active", "paused", "closing", "legacy")))).all()
    for exp in exps:
        spec = _spec(s, exp.approved_version_id)
        m = experiment_metrics(s, exp, t)
        obs = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in m.items()}
        bench_ret = None
        if snap.benchmark_nav is not None:
            bench_ret = float(snap.benchmark_nav / p.starting_cash - 1)
        excess = (m["return_on_budget"] - bench_ret) if (m["return_on_budget"] is not None and bench_ret is not None) else None
        values = {"experiment_return": m["return_on_budget"], "win_rate": m["win_rate"], "avg_trade_return": m["avg_trade_return"],
                  "excess_vs_benchmark": excess, "experiment_drawdown": min(0.0, m["return_on_budget"] or 0.0)}
        triggered = []
        for rule in spec.thesis_invalidation:
            v = values.get(rule.metric)
            if v is None or m["closed_trades"] < rule.min_closed_trades:
                continue
            ok = {"lt": v < rule.value, "lte": v <= rule.value, "gt": v > rule.value, "gte": v >= rule.value}[rule.op]
            if ok:
                triggered.append(f"{rule.metric} {rule.op} {rule.value} (observed {v:.4f})")
        status = exp.thesis_status
        if triggered:
            status = "invalidated"
            if exp.entries_allowed:
                exp.entries_allowed = False
                exp.status_reason = "pre-approved invalidation rule triggered: " + "; ".join(triggered)
                notify(s, p.user_id, "thesis", f"Thesis invalidated: {exp.name}",
                       "A pre-approved invalidation rule triggered; new entries stopped, approved exits continue. "
                       + "; ".join(triggered), f"invalid:{exp.id}", f"/experiments/{exp.id}", "urgent",
                       data_timestamp=cal.session_close_utc(t))
        elif m["closed_trades"] >= 10 and (m["avg_trade_return"] or 0) < 0:
            status = "challenged"
        elif m["open_positions"] or m["closed_trades"]:
            status = "active" if status in ("untested", "active") else status
        if status != exp.thesis_status:
            prior = exp.thesis_status
            exp.thesis_status = status
            if status == "challenged":
                notify(s, p.user_id, "thesis", f"Thesis challenged: {exp.name}",
                       f"Average closed-trade return is negative after {m['closed_trades']} trades. This may be noise; "
                       "the monthly review will assess it.", f"challenged:{exp.id}", f"/experiments/{exp.id}", "info")
            audit(s, p.user_id, "monitor", "thesis.status", "experiment", exp.id, {"status": prior}, {"status": status})
        if exp.status == "closing" and not open_lots(s, p.id, exp.id):
            exp.status, exp.ended_at = "closed", utcnow()
            exp.thesis_status = "completed" if exp.thesis_status in ("active", "untested") else exp.thesis_status
        elif (not exp.entries_allowed) and exp.ends_by and t >= exp.ends_by and not open_lots(s, p.id, exp.id) \
                and exp.status == "active":
            exp.status, exp.ended_at = "completed", utcnow()
            notify(s, p.user_id, "experiment", f"Experiment completed: {exp.name}", "All positions closed; see the journal.",
                   f"complete:{exp.id}", f"/experiments/{exp.id}")
        elif exp.status == "legacy" and not open_lots(s, p.id, exp.id):
            exp.status, exp.ended_at = "closed", utcnow()
        summary = (f"{m['open_positions']} open, {m['closed_trades']} closed trades; P&L {m['total_pnl']:+.2f} "
                   f"({(m['return_on_budget'] or 0):+.2%} of budget). Thesis: {status}.")
        s.execute(pg_insert(ThesisReview).values(
            id=__import__("uuid").uuid4().hex, user_id=p.user_id, experiment_id=exp.id, session=t, kind="daily", status=status,
            summary=summary, observations={**obs, "invalidation_triggered": triggered, "benchmark_return": bench_ret,
                                           "excess": excess},
            evidence_links=[], confidence="low" if m["closed_trades"] < 30 else "medium",
            counterargument="Small samples are dominated by noise; a profitable trade is not a validated hypothesis.",
            provider="deterministic", created_at=utcnow()).on_conflict_do_nothing(index_elements=["experiment_id", "session", "kind"]))
