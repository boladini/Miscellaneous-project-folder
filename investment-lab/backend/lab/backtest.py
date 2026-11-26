"""Deterministic historical backtester.

Uses the SAME rule interpreter (rules.evaluate), indicator definitions, execution model
(execution.fill_price / next-eligible-open) and risk checks (risk.check_entry) as the forward
paper simulator. Rules are fixed before testing (no optimisation), the chronological 60/20/20
partitions are reported separately, and the final holdout is never used for tuning.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import market_calendar as cal
from .execution import D, fill_price, q6, whole_shares
from .indicators import Bar, BarView, EarningsEvent, PositionContext, PrefixSeq, compute
from .risk import EntryContext, check_entry
from .rules import evaluate, first_true_reason, has_missing
from .strategy_schema import Indicator, StrategySpec, canonical_hash

CODE_VERSION = "backtest-engine-1.0.0"
MIN_TRADES_ROBUST = 30
MIN_HOLDOUT_TRADES = 5


@dataclass
class InstrumentData:
    symbol: str
    type: str
    sector: str | None
    bars: list[Bar]
    events: list[EarningsEvent] = field(default_factory=list)
    splits: dict[date, float] = field(default_factory=dict)
    dividends: dict[date, float] = field(default_factory=dict)


@dataclass
class Dataset:
    instruments: dict[str, InstrumentData]
    benchmark: InstrumentData | None
    provider: str
    adjustment_basis: str
    synthetic: bool
    event_point_in_time: bool = True
    survivorship_free: bool = False

    def manifest(self) -> dict:
        per = {}
        for s, d in self.instruments.items():
            per[s] = {"bars": len(d.bars), "first": d.bars[0].session.isoformat() if d.bars else None,
                      "last": d.bars[-1].session.isoformat() if d.bars else None, "events": len(d.events),
                      "splits": len(d.splits), "dividends": len(d.dividends)}
        fp = canonical_hash({s: [(b.session.isoformat(), b.open, b.close) for b in d.bars[-5:]] + [len(d.bars)]
                             for s, d in sorted(self.instruments.items())})
        return {"provider": self.provider, "adjustment_basis": self.adjustment_basis, "synthetic": self.synthetic,
                "event_point_in_time": self.event_point_in_time, "survivorship_free": self.survivorship_free,
                "benchmark": self.benchmark.symbol if self.benchmark else None, "instruments": per,
                "fingerprint": fp}


@dataclass
class _Lot:
    symbol: str
    qty: Decimal
    entry_price: Decimal
    cost: Decimal
    entry_session: date
    peak_close: float
    entry_index: int


@dataclass
class _Order:
    symbol: str
    side: str
    qty: Decimal
    signal_session: date
    expires_after: date
    reserved: Decimal = Decimal(0)
    lot: _Lot | None = None
    reason: str = ""


def eligible_universe(spec: StrategySpec, ds: Dataset) -> list[str]:
    syms = []
    for s, d in ds.instruments.items():
        if d.type not in spec.universe.asset_types:
            continue
        if spec.universe.symbols and s not in spec.universe.symbols:
            continue
        if s in spec.universe.exclude_symbols:
            continue
        syms.append(s)
    return sorted(syms)


def run_backtest(spec: StrategySpec, ds: Dataset, start: date | None = None, end: date | None = None,
                 starting_cash: Decimal = Decimal(10000), slippage_bps: float | None = None,
                 limits: dict | None = None, record_series: bool = True) -> dict:
    """Simulate one experiment inside an otherwise-cash portfolio. Returns metrics/trades/series."""
    slip = D(spec.execution.slippage_bps if slippage_bps is None else slippage_bps)
    commission = D(spec.execution.commission_per_order)
    universe = eligible_universe(spec, ds)
    if not universe:
        return {"status": "not_backtestable", "reason": "no instruments in the dataset match the strategy universe"}
    if "earnings_events" in spec.required_data and not any(ds.instruments[s].events for s in universe):
        return {"status": "not_backtestable", "reason": "strategy requires earnings events with as-known timestamps; none available"}

    all_sessions = sorted({b.session for s in universe for b in ds.instruments[s].bars})
    if start:
        all_sessions = [d for d in all_sessions if d >= start]
    if end:
        all_sessions = [d for d in all_sessions if d <= end]
    warm = spec.warmup_sessions()
    if len(all_sessions) < warm + 60:
        return {"status": "not_backtestable", "reason": f"insufficient history: {len(all_sessions)} sessions, need {warm + 60}"}

    idx: dict[str, dict[date, int]] = {s: {b.session: i for i, b in enumerate(ds.instruments[s].bars)} for s in universe}
    bench = ds.benchmark
    bidx = {b.session: i for i, b in enumerate(bench.bars)} if bench else {}

    cash = D(starting_cash)
    budget = q6(D(starting_cash) * D(spec.allocation.max_experiment_fraction))  # experiment sleeve capital
    lots: list[_Lot] = []
    pending: list[_Order] = []
    trades: list[dict] = []
    equity: list[tuple[date, float, float | None]] = []
    blocked = 0
    gross_traded = Decimal(0)
    exposure_sum = 0.0
    bench_shares = None
    first_trade_session = all_sessions[warm] if len(all_sessions) > warm else all_sessions[0]
    last_close: dict[str, float] = {}

    for si, t in enumerate(all_sessions):
        # 1) fills at today's open for orders generated at a prior close
        still = []
        for o in pending:
            i = idx[o.symbol].get(t)
            if i is None:
                if t > o.expires_after:  # no data => no fill; order expires (reservation released)
                    blocked += 1
                    continue
                still.append(o)
                continue
            bar = ds.instruments[o.symbol].bars[i]
            px = fill_price(D(bar.open), o.side, slip)
            if o.side == "buy":
                qty = o.qty
                cost = q6(qty * px + commission)
                if cost > cash:  # gap above reservation buffer: resize, never go negative
                    qty = whole_shares(cash - commission, px, spec.execution.fractional_shares)
                    cost = q6(qty * px + commission)
                if qty <= 0:
                    blocked += 1
                    continue
                cash -= cost
                gross_traded += qty * px
                lots.append(_Lot(o.symbol, qty, px, cost, t, bar.close, si))
            else:
                lot = o.lot
                proceeds = q6(lot.qty * px - commission)
                cash += proceeds
                gross_traded += lot.qty * px
                pnl = proceeds - lot.cost
                trades.append({"symbol": lot.symbol, "entry_session": lot.entry_session.isoformat(), "exit_session": t.isoformat(),
                               "entry_price": float(lot.entry_price), "exit_price": float(px), "qty": float(lot.qty),
                               "pnl": float(pnl), "return": float(pnl / lot.cost) if lot.cost else 0.0,
                               "sessions_held": si - lot.entry_index, "reason": o.reason})
                lots.remove(lot)
        pending = still

        # 2) corporate actions effective today (raw-price datasets only)
        if ds.adjustment_basis == "raw":
            for lot in lots:
                inst = ds.instruments[lot.symbol]
                if t in inst.splits:
                    r = D(inst.splits[t])
                    lot.qty = lot.qty * r
                    lot.entry_price = lot.entry_price / r
                    lot.peak_close = lot.peak_close / float(r)
                if t in inst.dividends and lot.entry_session < t:
                    cash += q6(lot.qty * D(inst.dividends[t]))

        # 3) mark to close
        holdings = Decimal(0)
        for lot in lots:
            i = idx[lot.symbol].get(t)
            if i is not None:
                last_close[lot.symbol] = ds.instruments[lot.symbol].bars[i].close
                lot.peak_close = max(lot.peak_close, last_close[lot.symbol])
            holdings += lot.qty * D(last_close.get(lot.symbol, float(lot.entry_price)))
        nav = cash + holdings
        sleeve = budget + (nav - D(starting_cash))  # metrics are on allocated capital, not diluted NAV
        bnav = None
        if bench and t >= first_trade_session:
            if t in bidx:
                bbar = bench.bars[bidx[t]]
                if bench_shares is None:  # benchmark bought at the first eligible open, same slippage
                    bench_shares = budget / fill_price(D(bbar.open), "buy", slip)
                bnav = float(bench_shares * D(bbar.close))
            elif equity:
                bnav = equity[-1][2]
        if t >= first_trade_session:
            equity.append((t, float(sleeve), bnav))
            exposure_sum += float(holdings / sleeve) if sleeve > 0 else 0.0
        if si >= len(all_sessions) - 1:
            break
        expiry = cal.add_sessions(t, spec.execution.order_expiry_sessions)

        # 4) exits evaluated on today's close -> sell at next open
        pending_lots = {id(o.lot) for o in pending if o.side == "sell"}
        for lot in lots:
            if id(lot) in pending_lots:
                continue
            inst = ds.instruments[lot.symbol]
            i = idx[lot.symbol].get(t)
            if i is None:
                continue
            view = BarView(PrefixSeq(inst.bars, i + 1), t, None, _known_events(inst, t))
            held = si - lot.entry_index
            if held < spec.holding.min_sessions:
                continue
            pos = PositionContext(float(lot.entry_price), lot.entry_session, held, lot.peak_close)
            hit, trace = evaluate(spec.exit, view, pos)
            if held >= spec.holding.max_sessions:
                hit, trace = True, {"indicator": "sessions_held", "value": held, "op": "gte",
                                    "threshold": spec.holding.max_sessions, "result": True}
            if hit:
                pending.append(_Order(lot.symbol, "sell", lot.qty, t, expiry, lot=lot, reason=first_true_reason(trace)))

        # 5) entries
        if si < warm or t < first_trade_session:
            continue
        held_syms = {lot.symbol for lot in lots} | {o.symbol for o in pending if o.side == "buy"}
        slots = spec.allocation.max_positions - len(lots) - sum(1 for o in pending if o.side == "buy")
        if slots <= 0:
            continue
        cands = []
        for s in universe:
            if s in held_syms:
                continue
            inst = ds.instruments[s]
            i = idx[s].get(t)
            if i is None:
                continue
            view = BarView(PrefixSeq(inst.bars, i + 1), t, None, _known_events(inst, t))
            ok, trace = evaluate(spec.entry, view)
            if not ok or has_missing(trace):
                continue
            rv = compute(spec.ranking.indicator, spec.ranking.params, view)
            if rv is None:
                continue
            cands.append((rv, s, view))
        cands.sort(key=lambda x: x[0], reverse=(spec.ranking.direction == "descending"))
        for rv, s, view in cands[:slots]:
            inst = ds.instruments[s]
            price = view.bars[-1].close
            adv = compute(Indicator.avg_dollar_volume, {"n": 20}, view)
            reserved = sum((o.reserved for o in pending if o.side == "buy"), Decimal(0))
            committed = sum((lot.cost for lot in lots), Decimal(0)) + reserved
            tick_exp = Decimal(0)
            sector_exp = None
            if inst.sector:
                sector_exp = sum((lot.qty * D(last_close.get(lot.symbol, 0)) for lot in lots
                                  if ds.instruments[lot.symbol].sector == inst.sector), Decimal(0))
            ctx = EntryContext(nav=nav, cash=cash, reserved=reserved, ticker_exposure=tick_exp, sector_exposure=sector_exp,
                               experiment_committed=committed, experiment_budget=budget,
                               instrument_type=inst.type, price=price, avg_dollar_volume_20d=adv, bar_age_sessions=0,
                               portfolio_paused=False, circuit_breaker=False, experiment_entries_allowed=True, global_paused=False,
                               binary_catalyst=spec.catalyst.binary, strategy_max_position_fraction=spec.allocation.max_position_fraction,
                               strategy_max_experiment_fraction=spec.allocation.max_experiment_fraction,
                               strategy_min_price=spec.universe.min_price, strategy_min_adv=spec.universe.min_avg_dollar_volume_20d)
            dec = check_entry(ctx, limits or {})
            if not dec.allowed:
                blocked += 1
                continue
            est = D(price) * (1 + slip / Decimal(10000))
            qty = whole_shares(dec.max_notional / Decimal("1.03"), est, spec.execution.fractional_shares)
            if qty <= 0:
                blocked += 1
                continue
            res = q6(qty * est * Decimal("1.03"))
            pending.append(_Order(s, "buy", qty, t, expiry, reserved=res))

    return {"status": "completed", "trades": trades, "equity": equity, "blocked_orders": blocked,
            "gross_traded": float(gross_traded), "avg_exposure": exposure_sum / max(1, len(equity)),
            "open_positions_at_end": len(lots), "universe": universe, "sessions": len(equity), "capital_basis": float(budget)}


def _known_events(inst: InstrumentData, t: date) -> list[EarningsEvent]:
    """Events whose as-known time is before the session's data-ready time (point-in-time)."""
    cutoff = cal.data_ready_utc(t)
    return [e for e in inst.events if e.known_at <= cutoff and e.event_date <= t]


# ------------------------------------------------------------------------------------------
# Metrics
# ------------------------------------------------------------------------------------------
def segment_metrics(equity: list[tuple[date, float, float | None]], trades: list[dict], start: date, end: date) -> dict:
    seg = [e for e in equity if start <= e[0] <= end]
    if len(seg) < 2:
        return {"sessions": len(seg), "total_return": None}
    navs = [e[1] for e in seg]
    rets = [navs[i] / navs[i - 1] - 1 for i in range(1, len(navs)) if navs[i - 1] > 0]
    total = navs[-1] / navs[0] - 1
    years = len(seg) / 252
    cagr = (1 + total) ** (1 / years) - 1 if years >= 1 and total > -1 else None
    vol = _stdev(rets) * math.sqrt(252) if len(rets) > 1 else None
    sharpe = (_mean(rets) / _stdev(rets) * math.sqrt(252)) if len(rets) > 1 and _stdev(rets) > 0 else None
    mdd = max_drawdown(navs)
    seg_trades = [t for t in trades if start.isoformat() <= t["exit_session"] <= end.isoformat()]
    wins = [t for t in seg_trades if t["pnl"] > 0]
    bench = [e[2] for e in seg if e[2] is not None]
    bench_ret = bench[-1] / bench[0] - 1 if len(bench) >= 2 and bench[0] else None
    return {
        "start": seg[0][0].isoformat(), "end": seg[-1][0].isoformat(), "sessions": len(seg),
        "total_return": total, "cagr": cagr, "volatility": vol, "sharpe": sharpe, "max_drawdown": mdd,
        "trade_count": len(seg_trades), "win_rate": (len(wins) / len(seg_trades)) if seg_trades else None,
        "avg_trade_return": _mean([t["return"] for t in seg_trades]) if seg_trades else None,
        "benchmark_return": bench_ret, "excess_return": (total - bench_ret) if bench_ret is not None else None,
    }


def max_drawdown(navs: list[float]) -> float | None:
    if not navs:
        return None
    peak, mdd = navs[0], 0.0
    for v in navs:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1 if peak > 0 else 0.0)
    return mdd


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def partitions(equity) -> dict[str, tuple[date, date]]:
    n = len(equity)
    a, b = int(n * 0.6), int(n * 0.8)
    if n < 10:
        return {}
    return {"train": (equity[0][0], equity[a - 1][0]), "validation": (equity[a][0], equity[b - 1][0]),
            "holdout": (equity[b][0], equity[-1][0])}


def perturb(spec: StrategySpec, factor: float) -> StrategySpec | None:
    """Scale every entry threshold by `factor` (parameter sensitivity; not used for selection)."""
    data = spec.canonical()

    def walk(n):
        if isinstance(n, dict):
            if "indicator" in n and "value" in n and "op" in n:
                n["value"] = n["value"] * factor
                if n.get("value2") is not None:
                    n["value2"] = n["value2"] * factor
                    if n["value2"] < n["value"]:
                        n["value"], n["value2"] = n["value2"], n["value"]
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(data["entry"])
    try:
        return StrategySpec.model_validate(data)
    except Exception:  # noqa: BLE001 — perturbation may leave valid ranges
        return None


def full_evaluation(spec: StrategySpec, ds: Dataset, starting_cash: Decimal = Decimal(10000), limits: dict | None = None,
                    years: int = 5, end: date | None = None) -> dict:
    """Base + stress-cost + sensitivity runs; partition metrics; robustness verdict; manifest."""
    last = end or max((d.bars[-1].session for d in ds.instruments.values() if d.bars), default=None)
    if last is None:
        return {"status": "not_backtestable", "reason": "no market data", "dataset_manifest": ds.manifest()}
    start = cal.next_session(date(last.year - years, last.month, min(last.day, 28)))
    base = run_backtest(spec, ds, start, last, starting_cash, None, limits)
    manifest = ds.manifest()
    params = {"start": start.isoformat(), "end": last.isoformat(), "starting_cash": float(starting_cash),
              "slippage_bps": spec.execution.slippage_bps, "stress_slippage_bps": spec.costs.stress_slippage_bps,
              "fill": "next eligible open after finalized-close signal", "risk_free": "0 (declared)",
              "partitions": "chronological 60/20/20; rules fixed ex-ante; holdout never used for tuning",
              "capital_basis": "returns are on the experiment's allocated capital (max_experiment_fraction x $10,000); "
                               "the benchmark is bought with the same capital at the first eligible open"}
    if base["status"] != "completed":
        return {**base, "parameters": params, "dataset_manifest": manifest, "code_version": CODE_VERSION, "limitations": []}
    eq, tr = base["equity"], base["trades"]
    parts = partitions(eq)
    metrics = {"full": segment_metrics(eq, tr, eq[0][0], eq[-1][0])}
    for k, (a, b) in parts.items():
        metrics[k] = segment_metrics(eq, tr, a, b)
    metrics["full"].update({"turnover": base["gross_traded"] / base["capital_basis"] / max(1e-9, len(eq) / 252),
                            "avg_exposure": base["avg_exposure"], "blocked_orders": base["blocked_orders"]})
    stress = run_backtest(spec, ds, start, last, starting_cash, spec.costs.stress_slippage_bps, limits)
    if stress["status"] == "completed":
        metrics["stress_cost"] = segment_metrics(stress["equity"], stress["trades"], eq[0][0], eq[-1][0])
    sens = {}
    for f in (0.8, 1.2):
        p = perturb(spec, f)
        if p is None:
            sens[str(f)] = None
            continue
        r = run_backtest(p, ds, start, last, starting_cash, None, limits)
        sens[str(f)] = segment_metrics(r["equity"], r["trades"], eq[0][0], eq[-1][0])["total_return"] if r["status"] == "completed" else None
    metrics["sensitivity_entry_threshold_x"] = sens

    limitations = []
    if not ds.survivorship_free:
        limitations.append("Universe is a fixed present-day list; delisted/acquired names are under-represented "
                           "(survivorship bias likely flatters results).")
    if ds.synthetic:
        limitations.append("Dataset is SYNTHETIC demo data; results say nothing about real markets.")
    if ds.adjustment_basis != "raw":
        limitations.append(f"Provider prices are {ds.adjustment_basis}; dividends not credited separately.")
    if "earnings_events" in spec.required_data and not ds.event_point_in_time:
        limitations.append("Earnings estimates are the provider's latest record, not certified point-in-time; "
                           "event-study claims are limited.")
    hold = metrics.get("holdout", {})
    val = metrics.get("validation", {})
    full = metrics["full"]
    n_trades = full.get("trade_count", 0)
    robust_checks = {
        "min_trades": n_trades >= MIN_TRADES_ROBUST,
        "holdout_trades": (hold.get("trade_count") or 0) >= MIN_HOLDOUT_TRADES,
        "validation_excess_positive": (val.get("excess_return") or -1) > 0,
        "holdout_excess_positive": (hold.get("excess_return") or -1) > 0,
        "stress_cost_positive": ((metrics.get("stress_cost") or {}).get("total_return") or -1) > 0,
        "sensitivity_same_sign": all(v is not None and (v > 0) == ((full.get("total_return") or 0) > 0) for v in sens.values()),
    }
    if n_trades < MIN_TRADES_ROBUST:
        limitations.append(f"Only {n_trades} closed trades (< {MIN_TRADES_ROBUST}); robustness cannot be claimed.")
    series = {"equity": [[d.isoformat(), round(v, 2), (round(b, 2) if b is not None else None)] for d, v, b in eq],
              "trades": tr[-300:]}
    return {"status": "completed", "metrics": metrics, "robustness": robust_checks,
            "robust": all(robust_checks.values()), "parameters": params, "dataset_manifest": manifest,
            "code_version": CODE_VERSION, "limitations": limitations, "series": series,
            "universe": base["universe"], "rule_hash": spec.rule_hash()}
