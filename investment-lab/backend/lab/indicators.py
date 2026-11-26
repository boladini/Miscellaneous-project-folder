"""Exact indicator calculations over a point-in-time bar view.

A BarView only contains bars with session <= decision session; construct it with `asof` and any
later bar raises LookAheadError. Events are filtered by `known_at <= decision_time`.
Indicators return None when warmup history is insufficient (never a fabricated value).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from collections.abc import Sequence

from .strategy_schema import Indicator


class LookAheadError(RuntimeError):
    pass


class PrefixSeq(Sequence):
    """Zero-copy view of seq[:end] (avoids O(n^2) slicing in backtests)."""

    def __init__(self, seq, end: int):
        self.seq, self.end = seq, end

    def __len__(self):
        return self.end

    def __getitem__(self, i):
        if isinstance(i, slice):
            start, stop, step = i.indices(self.end)
            return self.seq[start:stop:step]
        if i < 0:
            i += self.end
        if not 0 <= i < self.end:
            raise IndexError(i)
        return self.seq[i]


def _lookback(ind, params) -> int:
    if ind in (Indicator.earnings_surprise_pct, Indicator.sessions_since_earnings):
        return 400
    n = max(params.values()) if params else 1
    return 3 * n + 3


@dataclass(frozen=True)
class Bar:
    session: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class EarningsEvent:
    event_date: date
    known_at: datetime
    surprise_pct: float | None


@dataclass
class PositionContext:
    entry_price: float
    entry_session: date
    sessions_held: int
    peak_close: float


@dataclass
class BarView:
    bars: Sequence[Bar]
    asof: date
    decision_time: datetime | None = None
    events: Sequence[EarningsEvent] = field(default_factory=tuple)

    def __post_init__(self):
        if self.bars and self.bars[-1].session > self.asof:
            raise LookAheadError(f"bar {self.bars[-1].session} is after decision session {self.asof}")
        if self.decision_time is not None:
            for e in self.events:
                if e.known_at > self.decision_time:
                    raise LookAheadError("event not yet known at decision time was supplied")

    def lagged(self, lag: int) -> "BarView | None":
        if lag == 0:
            return self
        if len(self.bars) <= lag:
            return None
        bars = PrefixSeq(self.bars, len(self.bars) - lag)
        return BarView(bars, bars[-1].session, self.decision_time, self.events)


def _sma(xs: Sequence[float], n: int) -> float | None:
    if len(xs) < n:
        return None
    return sum(xs[-n:]) / n


def rsi_wilder(closes: Sequence[float], n: int) -> float | None:
    if len(closes) < 3 * n + 1:
        return None
    window = closes[-(3 * n + 1):]
    deltas = [window[i] - window[i - 1] for i in range(1, len(window))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):  # noqa: E741
        avg_g = (avg_g * (n - 1) + g) / n
        avg_l = (avg_l * (n - 1) + l) / n
    if avg_l == 0:
        return 100.0 if avg_g > 0 else 50.0
    rs = avg_g / avg_l
    return 100.0 - 100.0 / (1.0 + rs)


def compute(ind: Indicator, params: dict[str, int], view: BarView | None,
            pos: PositionContext | None = None, sessions_since: dict[date, int] | None = None) -> float | None:
    if view is None or not view.bars:
        return None
    bars = view.bars[-_lookback(ind, params):]
    closes = [b.close for b in bars]
    c = closes[-1]
    if ind == Indicator.close:
        return c
    if ind == Indicator.return_nd:
        n = params["n"]
        if len(closes) < n + 1 or closes[-1 - n] <= 0:
            return None
        return c / closes[-1 - n] - 1
    if ind == Indicator.sma_ratio:
        s = _sma(closes, params["n"])
        return None if not s else c / s - 1
    if ind == Indicator.sma_cross:
        f, s = _sma(closes, params["fast"]), _sma(closes, params["slow"])
        return None if not f or not s else f / s - 1
    if ind == Indicator.rsi:
        return rsi_wilder(closes, params["n"])
    if ind == Indicator.volatility:
        n = params["n"]
        if len(closes) < n + 1:
            return None
        w = closes[-(n + 1):]
        rets = [math.log(w[i] / w[i - 1]) for i in range(1, len(w)) if w[i - 1] > 0 and w[i] > 0]
        if len(rets) < 2:
            return None
        m = sum(rets) / len(rets)
        var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var) * math.sqrt(252)
    if ind == Indicator.avg_dollar_volume:
        n = params["n"]
        if len(bars) < n:
            return None
        return sum(b.close * b.volume for b in bars[-n:]) / n
    if ind == Indicator.dist_from_high:
        n = params["n"]
        if len(bars) < n:
            return None
        hi = max(b.high for b in bars[-n:])
        return c / hi - 1 if hi > 0 else None
    if ind == Indicator.volume_ratio:
        n = params["n"]
        if len(bars) < n + 1:
            return None
        avg = sum(b.volume for b in bars[-(n + 1):-1]) / n
        return bars[-1].volume / avg if avg > 0 else None
    if ind == Indicator.gap_pct:
        if len(bars) < 2 or bars[-2].close <= 0:
            return None
        return bars[-1].open / bars[-2].close - 1
    if ind in (Indicator.earnings_surprise_pct, Indicator.sessions_since_earnings):
        known = [e for e in view.events if e.event_date <= view.asof
                 and (view.decision_time is None or e.known_at <= view.decision_time)]
        if not known:
            return None
        last = max(known, key=lambda e: e.event_date)
        sessions = [b.session for b in bars]
        since = sum(1 for s in sessions if s > last.event_date)
        if ind == Indicator.sessions_since_earnings:
            return float(since)
        if since > params["within_sessions"]:
            return None
        return last.surprise_pct
    if ind in (Indicator.return_since_entry, Indicator.sessions_held, Indicator.drawdown_from_peak):
        if pos is None:
            return None
        if ind == Indicator.return_since_entry:
            return c / pos.entry_price - 1 if pos.entry_price > 0 else None
        if ind == Indicator.sessions_held:
            return float(pos.sessions_held)
        peak = max(pos.peak_close, c)
        return c / peak - 1 if peak > 0 else None
    raise ValueError(f"unsupported indicator {ind}")
