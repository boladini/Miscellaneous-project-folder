"""Versioned, declarative strategy schema (v1).

Every rule input is an observable, timestamped indicator computed only from data available at the
decision time. There is no free-form expression language, no SQL and no code: indicators and
operators are closed enums, parameters are range-checked, and nested boolean groups are depth-limited.
"""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1
MAX_DEPTH = 4


class Family(str, Enum):
    contrarian = "contrarian"
    growth = "growth"
    asymmetric = "asymmetric"
    event_driven = "event_driven"
    ai_generated = "ai_generated"


class Horizon(str, Enum):
    swing = "swing"
    long_term = "long_term"


class Indicator(str, Enum):
    close = "close"
    return_nd = "return_nd"
    sma_ratio = "sma_ratio"
    sma_cross = "sma_cross"
    rsi = "rsi"
    volatility = "volatility"
    avg_dollar_volume = "avg_dollar_volume"
    dist_from_high = "dist_from_high"
    volume_ratio = "volume_ratio"
    gap_pct = "gap_pct"
    earnings_surprise_pct = "earnings_surprise_pct"
    sessions_since_earnings = "sessions_since_earnings"
    # position-level (exit rules only)
    return_since_entry = "return_since_entry"
    sessions_held = "sessions_held"
    drawdown_from_peak = "drawdown_from_peak"


POSITION_INDICATORS = {Indicator.return_since_entry, Indicator.sessions_held, Indicator.drawdown_from_peak}
EVENT_INDICATORS = {Indicator.earnings_surprise_pct, Indicator.sessions_since_earnings}

# name -> (required params with (min, max), unit, value range)
INDICATOR_SPECS: dict[Indicator, dict[str, Any]] = {
    Indicator.close: {"params": {}, "unit": "usd", "range": (0, 1e7), "doc": "Finalized session close."},
    Indicator.return_nd: {"params": {"n": (1, 252)}, "unit": "fraction", "range": (-1, 20),
                          "doc": "close[t]/close[t-n]-1; warmup n+1 bars."},
    Indicator.sma_ratio: {"params": {"n": (5, 252)}, "unit": "fraction", "range": (-1, 20),
                          "doc": "close[t]/SMA(close,n)-1; warmup n bars."},
    Indicator.sma_cross: {"params": {"fast": (2, 100), "slow": (5, 252)}, "unit": "fraction", "range": (-1, 20),
                          "doc": "SMA(fast)/SMA(slow)-1; warmup slow bars."},
    Indicator.rsi: {"params": {"n": (2, 50)}, "unit": "index_0_100", "range": (0, 100),
                    "doc": "Wilder RSI over n periods; warmup 3n bars for smoothing."},
    Indicator.volatility: {"params": {"n": (5, 252)}, "unit": "annualized_fraction", "range": (0, 20),
                           "doc": "stdev(log returns, n, ddof=1)*sqrt(252); warmup n+1 bars."},
    Indicator.avg_dollar_volume: {"params": {"n": (5, 252)}, "unit": "usd", "range": (0, 1e13),
                                  "doc": "mean(close*volume) over n bars."},
    Indicator.dist_from_high: {"params": {"n": (5, 252)}, "unit": "fraction", "range": (-1, 0),
                               "doc": "close[t]/max(high, n)-1 (<=0)."},
    Indicator.volume_ratio: {"params": {"n": (5, 252)}, "unit": "ratio", "range": (0, 1000),
                             "doc": "volume[t]/mean(volume over prior n bars)."},
    Indicator.gap_pct: {"params": {}, "unit": "fraction", "range": (-1, 20),
                        "doc": "open[t]/close[t-1]-1."},
    Indicator.earnings_surprise_pct: {"params": {"within_sessions": (1, 60)}, "unit": "fraction", "range": (-100, 100),
                                      "doc": "(reported-estimate)/|estimate| of latest earnings known by decision time "
                                             "and reported within the window; null otherwise."},
    Indicator.sessions_since_earnings: {"params": {}, "unit": "sessions", "range": (0, 1000),
                                        "doc": "Sessions since latest earnings known by decision time."},
    Indicator.return_since_entry: {"params": {}, "unit": "fraction", "range": (-1, 20),
                                   "doc": "close[t]/entry_fill_price-1."},
    Indicator.sessions_held: {"params": {}, "unit": "sessions", "range": (0, 2000),
                              "doc": "Sessions since fill session (fill session = 0)."},
    Indicator.drawdown_from_peak: {"params": {}, "unit": "fraction", "range": (-1, 0),
                                   "doc": "close[t]/max(close since entry)-1."},
}


class Op(str, Enum):
    lt = "lt"
    lte = "lte"
    gt = "gt"
    gte = "gte"
    between = "between"


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    indicator: Indicator
    params: dict[str, int] = Field(default_factory=dict)
    op: Op
    value: float
    value2: float | None = None
    lag: int = 0  # evaluate indicator `lag` sessions ago; must be >= 0 (no look-ahead)

    @model_validator(mode="after")
    def _check(self) -> "Condition":
        spec = INDICATOR_SPECS[self.indicator]
        if self.lag < 0:
            raise ValueError("look-ahead rejected: lag must be >= 0 (future observations are not available)")
        if self.lag > 20:
            raise ValueError("lag must be <= 20")
        required = spec["params"]
        if set(self.params) != set(required):
            raise ValueError(f"{self.indicator.value} requires params {sorted(required)} got {sorted(self.params)}")
        for k, (lo, hi) in required.items():
            if not lo <= self.params[k] <= hi:
                raise ValueError(f"{self.indicator.value}.{k}={self.params[k]} outside [{lo},{hi}]")
        if self.indicator == Indicator.sma_cross and self.params["fast"] >= self.params["slow"]:
            raise ValueError("sma_cross requires fast < slow")
        lo, hi = spec["range"]
        vals = [self.value] + ([self.value2] if self.value2 is not None else [])
        for v in vals:
            if not lo <= v <= hi:
                raise ValueError(f"{self.indicator.value} threshold {v} outside unit range [{lo},{hi}] ({spec['unit']})")
        if self.op == Op.between:
            if self.value2 is None or self.value2 <= self.value:
                raise ValueError("between requires value < value2")
        elif self.value2 is not None:
            raise ValueError("value2 only allowed with between")
        return self


class AllGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    all: list["Node"] = Field(min_length=1, max_length=12)


class AnyGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    any: list["Node"] = Field(min_length=1, max_length=12)


class NotGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    not_: "Node" = Field(alias="not")


Node = Annotated[Union[Condition, AllGroup, AnyGroup, NotGroup], Field(union_mode="left_to_right")]
AllGroup.model_rebuild()
AnyGroup.model_rebuild()
NotGroup.model_rebuild()


def iter_conditions(node: Any, depth: int = 1):
    if depth > MAX_DEPTH:
        raise ValueError(f"rule nesting deeper than {MAX_DEPTH}")
    if isinstance(node, Condition):
        yield node
    elif isinstance(node, AllGroup):
        for c in node.all:
            yield from iter_conditions(c, depth + 1)
    elif isinstance(node, AnyGroup):
        for c in node.any:
            yield from iter_conditions(c, depth + 1)
    elif isinstance(node, NotGroup):
        yield from iter_conditions(node.not_, depth + 1)


class Universe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_types: list[Literal["stock", "etf"]] = Field(min_length=1)
    market: Literal["US"] = "US"
    min_price: float = Field(default=5, ge=1, le=10000)
    min_avg_dollar_volume_20d: float = Field(default=5_000_000, ge=0)
    symbols: list[str] | None = Field(default=None, max_length=200)
    exclude_symbols: list[str] = Field(default_factory=list)


class Ranking(BaseModel):
    model_config = ConfigDict(extra="forbid")
    indicator: Indicator
    params: dict[str, int] = Field(default_factory=dict)
    direction: Literal["ascending", "descending"]

    @model_validator(mode="after")
    def _check(self):
        if self.indicator in POSITION_INDICATORS:
            raise ValueError("ranking cannot use position-level indicators")
        Condition(indicator=self.indicator, params=self.params, op=Op.gte, value=INDICATOR_SPECS[self.indicator]["range"][0])
        return self


class Allocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_experiment_fraction: float = Field(gt=0, le=0.30)
    max_position_fraction: float = Field(gt=0, le=0.25)
    max_positions: int = Field(ge=1, le=20)


class Holding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_sessions: int = Field(default=0, ge=0)
    max_sessions: int = Field(ge=2, le=800)


class Execution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal: Literal["finalized_daily_close"] = "finalized_daily_close"
    fill: Literal["next_eligible_open"] = "next_eligible_open"
    order_type: Literal["market"] = "market"
    slippage_bps: float = Field(default=10, ge=0, le=200)
    commission_per_order: float = Field(default=0, ge=0, le=50)
    order_expiry_sessions: int = Field(default=3, ge=1, le=10)
    fractional_shares: bool = False


class Costs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stress_slippage_bps: float = Field(default=30, ge=0, le=500)


class Catalyst(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["none", "earnings"] = "none"
    binary: bool = False
    description: str = Field(default="", max_length=500)


class InvalidationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: Literal["experiment_return", "experiment_drawdown", "win_rate", "avg_trade_return", "excess_vs_benchmark"]
    op: Literal["lt", "lte", "gt", "gte"]
    value: float = Field(ge=-1, le=10)
    min_closed_trades: int = Field(default=0, ge=0, le=500)
    description: str = Field(default="", max_length=300)


class DataPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on_missing: Literal["block"] = "block"
    max_bar_age_sessions: int = Field(default=1, ge=0, le=5)


class Prediction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: Literal["avg_trade_return", "excess_vs_benchmark", "win_rate"]
    direction: Literal["positive", "above"]
    threshold: float = Field(ge=-1, le=10)
    horizon_sessions: int = Field(ge=1, le=800)


REQUIRED_DATA = Literal["daily_ohlcv", "instrument_metadata", "earnings_events", "corporate_actions"]


class StrategySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    name: str = Field(min_length=3, max_length=120)
    family: Family
    horizon: Horizon
    universe: Universe
    required_data: list[REQUIRED_DATA] = Field(min_length=1)
    entry: Node
    ranking: Ranking
    allocation: Allocation
    exit: Node
    holding: Holding
    execution: Execution = Field(default_factory=Execution)
    costs: Costs = Field(default_factory=Costs)
    catalyst: Catalyst = Field(default_factory=Catalyst)
    thesis_invalidation: list[InvalidationRule] = Field(default_factory=list, max_length=6)
    data_policy: DataPolicy = Field(default_factory=DataPolicy)
    hypothesis: str = Field(min_length=10, max_length=1500)
    prediction: Prediction

    @field_validator("universe")
    @classmethod
    def _uni(cls, u: Universe):
        if u.symbols:
            u.symbols = [s.upper() for s in u.symbols]
        return u

    @model_validator(mode="after")
    def _semantic(self) -> "StrategySpec":
        entry_conds = list(iter_conditions(self.entry))
        exit_conds = list(iter_conditions(self.exit))
        for c in entry_conds:
            if c.indicator in POSITION_INDICATORS:
                raise ValueError(f"entry rules cannot use position-level indicator {c.indicator.value}")
        uses_events = any(c.indicator in EVENT_INDICATORS for c in entry_conds + exit_conds) or self.ranking.indicator in EVENT_INDICATORS
        if uses_events and "earnings_events" not in self.required_data:
            raise ValueError("event indicators require 'earnings_events' in required_data")
        if "daily_ohlcv" not in self.required_data:
            raise ValueError("daily_ohlcv is required for all v1 strategies")
        # Horizon bounds (PRD defaults)
        if self.horizon == Horizon.swing and not (2 <= self.holding.max_sessions <= 60):
            raise ValueError("swing horizon requires max holding of 2-60 sessions")
        if self.horizon == Horizon.long_term and not (126 <= self.holding.max_sessions <= 756):
            raise ValueError("long_term horizon requires max holding of 126-756 sessions (6-36 months)")
        if self.holding.min_sessions >= self.holding.max_sessions:
            raise ValueError("min_sessions must be < max_sessions")
        # Exit must include a measurable holding limit
        if not any(c.indicator == Indicator.sessions_held for c in exit_conds):
            raise ValueError("exit rules must include a sessions_held time limit")
        if self.allocation.max_position_fraction > self.allocation.max_experiment_fraction:
            raise ValueError("max_position_fraction cannot exceed max_experiment_fraction")
        if self.family == Family.event_driven and self.catalyst.binary and self.allocation.max_position_fraction > 0.05:
            raise ValueError("binary catalyst positions are capped at 5% of NAV")
        if self.family == Family.asymmetric and self.allocation.max_experiment_fraction > 0.10:
            raise ValueError("asymmetric bets require bounded allocation (<=10% of NAV)")
        return self

    def canonical(self) -> dict:
        return json.loads(self.model_dump_json(by_alias=True))

    def rule_hash(self) -> str:
        return canonical_hash(self.canonical())

    def warmup_sessions(self) -> int:
        w = 21
        for c in list(iter_conditions(self.entry)) + list(iter_conditions(self.exit)):
            w = max(w, indicator_warmup(c.indicator, c.params) + c.lag)
        w = max(w, indicator_warmup(self.ranking.indicator, self.ranking.params))
        return w


def indicator_warmup(ind: Indicator, params: dict[str, int]) -> int:
    if ind in (Indicator.return_nd, Indicator.volatility):
        return params["n"] + 1
    if ind in (Indicator.sma_ratio, Indicator.avg_dollar_volume, Indicator.dist_from_high):
        return params["n"]
    if ind == Indicator.volume_ratio:
        return params["n"] + 1
    if ind == Indicator.sma_cross:
        return params["slow"]
    if ind == Indicator.rsi:
        return params["n"] * 3 + 1
    if ind == Indicator.gap_pct:
        return 2
    return 1


def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def validate_spec(data: dict) -> StrategySpec:
    """Validate untrusted (e.g. model-generated) strategy JSON. Raises pydantic.ValidationError."""
    spec = StrategySpec.model_validate(data)
    list(iter_conditions(spec.entry))  # depth check
    list(iter_conditions(spec.exit))
    return spec


def describe_node(node: Any) -> str:
    if isinstance(node, dict):
        node = _coerce(node)
    if isinstance(node, Condition):
        p = ",".join(f"{k}={v}" for k, v in node.params.items())
        name = f"{node.indicator.value}({p})" if p else node.indicator.value
        if node.lag:
            name += f"[lag {node.lag}]"
        sym = {"lt": "<", "lte": "≤", "gt": ">", "gte": "≥"}.get(node.op.value)
        if node.op == Op.between:
            return f"{node.value:g} ≤ {name} ≤ {node.value2:g}"
        return f"{name} {sym} {node.value:g}"
    if isinstance(node, AllGroup):
        return "(" + " AND ".join(describe_node(c) for c in node.all) + ")"
    if isinstance(node, AnyGroup):
        return "(" + " OR ".join(describe_node(c) for c in node.any) + ")"
    if isinstance(node, NotGroup):
        return "NOT " + describe_node(node.not_)
    return str(node)


def _coerce(d: dict):
    if "all" in d:
        return AllGroup.model_validate(d)
    if "any" in d:
        return AnyGroup.model_validate(d)
    if "not" in d:
        return NotGroup.model_validate(d)
    return Condition.model_validate(d)


def json_schema() -> dict:
    return StrategySpec.model_json_schema(by_alias=True)
