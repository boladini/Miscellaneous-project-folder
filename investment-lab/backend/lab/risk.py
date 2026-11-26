"""Risk engine. Enforced server-side inside the execution transaction (and in backtests).

The stricter of the portfolio policy and the strategy's approved limits always prevails.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .execution import D

DEFAULT_LIMITS = {
    "max_ticker_fraction": 0.10,
    "max_etf_fraction": 0.25,
    "max_sector_fraction": 0.20,
    "max_experiment_fraction": 0.30,
    "max_binary_event_fraction": 0.05,
    "min_cash_fraction": 0.10,
    "min_price": 5.0,
    "min_avg_dollar_volume_20d": 5_000_000.0,
    "drawdown_circuit_breaker": 0.15,
    "max_bar_age_sessions": 1,
    "slippage_bps": 10.0,
    "order_expiry_sessions": 3,
}


@dataclass
class EntryContext:
    nav: Decimal
    cash: Decimal
    reserved: Decimal
    ticker_exposure: Decimal  # consolidated marked value + pending buys across experiments
    sector_exposure: Decimal | None  # None when sector data is unreliable/unknown
    experiment_committed: Decimal  # open cost basis + pending buys of this experiment
    experiment_budget: Decimal
    instrument_type: str
    price: float | None
    avg_dollar_volume_20d: float | None
    bar_age_sessions: int | None
    portfolio_paused: bool
    circuit_breaker: bool
    experiment_entries_allowed: bool
    global_paused: bool
    binary_catalyst: bool
    strategy_max_position_fraction: float
    strategy_max_experiment_fraction: float
    strategy_min_price: float
    strategy_min_adv: float
    etf_approved: bool = True


@dataclass
class RiskDecision:
    allowed: bool
    max_notional: Decimal
    reasons: list[str] = field(default_factory=list)
    binding_limit: str | None = None


def check_entry(ctx: EntryContext, limits: dict) -> RiskDecision:
    lim = {**DEFAULT_LIMITS, **(limits or {})}
    reasons: list[str] = []
    if ctx.global_paused:
        reasons.append("global pause active")
    if ctx.portfolio_paused:
        reasons.append("portfolio paused")
    if ctx.circuit_breaker:
        reasons.append("drawdown circuit breaker active")
    if not ctx.experiment_entries_allowed:
        reasons.append("experiment entries paused")
    if ctx.price is None or ctx.bar_age_sessions is None:
        reasons.append("no quote available")
    elif ctx.bar_age_sessions > int(lim["max_bar_age_sessions"]):
        reasons.append(f"stale quote ({ctx.bar_age_sessions} sessions old)")
    min_price = max(float(lim["min_price"]), ctx.strategy_min_price)
    if ctx.price is not None and ctx.instrument_type == "stock" and ctx.price < min_price:
        reasons.append(f"price {ctx.price:.2f} below minimum {min_price}")
    min_adv = max(float(lim["min_avg_dollar_volume_20d"]), ctx.strategy_min_adv)
    if ctx.avg_dollar_volume_20d is None:
        reasons.append("insufficient liquidity history")
    elif ctx.avg_dollar_volume_20d < min_adv:
        reasons.append(f"20d avg dollar volume {ctx.avg_dollar_volume_20d:,.0f} below {min_adv:,.0f}")
    if ctx.instrument_type == "etf" and not ctx.etf_approved:
        reasons.append("ETF not approved for this experiment")
    if reasons:
        return RiskDecision(False, Decimal(0), reasons)

    nav = D(ctx.nav)
    caps: dict[str, Decimal] = {}
    per_pos = min(float(ctx.strategy_max_position_fraction),
                  float(lim["max_etf_fraction"] if ctx.instrument_type == "etf" else lim["max_ticker_fraction"]))
    if ctx.binary_catalyst:
        per_pos = min(per_pos, float(lim["max_binary_event_fraction"]))
    caps["position"] = nav * D(per_pos)
    ticker_cap = float(lim["max_etf_fraction"] if ctx.instrument_type == "etf" else lim["max_ticker_fraction"])
    caps["ticker_consolidated"] = nav * D(ticker_cap) - D(ctx.ticker_exposure)
    if ctx.sector_exposure is not None:
        caps["sector"] = nav * D(lim["max_sector_fraction"]) - D(ctx.sector_exposure)
    exp_cap = min(D(ctx.experiment_budget), nav * D(min(float(lim["max_experiment_fraction"]),
                                                        float(ctx.strategy_max_experiment_fraction))))
    caps["experiment_budget"] = exp_cap - D(ctx.experiment_committed)
    free_cash = D(ctx.cash) - D(ctx.reserved)
    caps["min_cash"] = free_cash - nav * D(lim["min_cash_fraction"])
    caps["cash"] = free_cash
    binding = min(caps, key=lambda k: caps[k])
    mx = caps[binding]
    if mx <= 0:
        return RiskDecision(False, Decimal(0), [f"limit reached: {binding}"], binding)
    return RiskDecision(True, mx, [], binding)
