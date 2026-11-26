"""Market-data provider interface and adapters.

Capabilities, licensing, delay, quotas and coverage MUST be verified by the owner at deployment time;
the notes below reflect what was verified during implementation (see docs/PROVIDERS.md).
"""
from __future__ import annotations

import csv
import io
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from hashlib import sha256

from .. import market_calendar as cal
from ..config import get_settings
from .http import ProviderError, get


@dataclass
class InstrumentMeta:
    symbol: str
    type: str = "stock"
    name: str | None = None
    sector: str | None = None
    exchange: str | None = None


@dataclass
class BarRecord:
    session: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    observed_at: datetime
    quality: str = "ok"


@dataclass
class ActionRecord:
    type: str  # split|dividend|delisting
    ex_date: date
    ratio: float | None = None
    amount: float | None = None
    pay_date: date | None = None
    known_at: datetime | None = None
    payload: dict = field(default_factory=dict)


@dataclass
class EventRecord:
    type: str
    event_date: date
    known_at: datetime
    payload: dict


class NotSupported(Exception):
    pass


class MarketDataProvider:
    name = "base"
    adjustment_basis = "raw"  # raw|split_adjusted|split_dividend_adjusted
    synthetic = False
    capabilities: dict = {}

    def instruments(self, symbols: list[str]) -> list[InstrumentMeta]:
        return [InstrumentMeta(s, "etf" if s in KNOWN_ETFS else "stock") for s in symbols]

    def universe(self) -> list[str]:
        return get_settings().universe

    def benchmark(self) -> str:
        return get_settings().benchmark_symbol

    def daily_bars(self, symbol: str, start: date, end: date) -> list[BarRecord]:
        raise NotImplementedError

    def corporate_actions(self, symbol: str, start: date, end: date) -> list[ActionRecord]:
        raise NotSupported

    def earnings_events(self, symbol: str) -> list[EventRecord]:
        raise NotSupported

    def configured(self) -> tuple[bool, str]:
        return True, "ok"


KNOWN_ETFS = {"SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLV", "XLE", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE", "XLC"}
LIVE_SECTORS = {  # static GICS-style sector map for the default universe (owner should verify/refresh)
    "AAPL": "Information Technology", "MSFT": "Information Technology", "NVDA": "Information Technology",
    "AVGO": "Information Technology", "ORCL": "Information Technology", "CSCO": "Information Technology",
    "ADBE": "Information Technology", "CRM": "Information Technology", "AMD": "Information Technology",
    "INTC": "Information Technology", "AMZN": "Consumer Discretionary", "HD": "Consumer Discretionary",
    "GOOGL": "Communication Services", "META": "Communication Services", "NFLX": "Communication Services",
    "JPM": "Financials", "V": "Financials", "MA": "Financials", "BAC": "Financials",
    "JNJ": "Health Care", "UNH": "Health Care", "MRK": "Health Care", "ABBV": "Health Care",
    "PG": "Consumer Staples", "KO": "Consumer Staples", "PEP": "Consumer Staples", "COST": "Consumer Staples",
    "WMT": "Consumer Staples", "XOM": "Energy", "CVX": "Energy",
}


def _close_ts(d: date) -> datetime:
    return cal.session_close_utc(d)


# --------------------------------------------------------------------------------------------
# Deterministic synthetic provider (DEMO ONLY — prices are generated, never real observations)
# --------------------------------------------------------------------------------------------
DEMO_START = date(2019, 1, 2)
DEMO_SECTORS = ["Synthetic Tech", "Synthetic Health", "Synthetic Energy", "Synthetic Finance",
                "Synthetic Consumer", "Synthetic Industrials"]
DEMO_STOCKS = [f"SYN{i:02d}" for i in range(1, 25)]
DEMO_ETFS = ["SYNETF1", "SYNETF2", "SYNETF3"]
DEMO_BENCH = "SYNBM"
DEMO_SPLIT = ("SYN05", date(2023, 6, 1), 2.0)
DEMO_DIVIDENDS = {"SYN02": 0.005, "SYN10": 0.006, DEMO_BENCH: 0.0035}
DEMO_DELIST = ("SYN24", date(2024, 3, 15))
DEMO_HALT = ("SYN23", date(2025, 9, 8), 5)
DEMO_ILLIQUID = "SYN22"


def _seed(*parts) -> int:
    return int(sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


class DemoMarketProvider(MarketDataProvider):
    name = "demo-synthetic"
    adjustment_basis = "raw"
    synthetic = True
    capabilities = {"bars": True, "corporate_actions": True, "earnings_events": True, "delay": "none (synthetic)",
                    "note": "Deterministic synthetic series for demonstration. NOT real market data."}

    def __init__(self, now_fn=None):
        self._now = now_fn or (lambda: datetime.now(timezone.utc))

    def universe(self) -> list[str]:
        return DEMO_STOCKS + DEMO_ETFS + [DEMO_BENCH]

    def benchmark(self) -> str:
        return DEMO_BENCH

    def instruments(self, symbols):
        out = []
        for s in symbols:
            if s in DEMO_ETFS or s == DEMO_BENCH:
                out.append(InstrumentMeta(s, "etf", f"Synthetic ETF {s}", None, "SYNTH"))
            else:
                idx = int(s[3:]) if s[3:].isdigit() else 0
                out.append(InstrumentMeta(s, "stock", f"Synthetic Co {s}", DEMO_SECTORS[idx % 6], "SYNTH"))
        return out

    def _last_session(self) -> date:
        s = get_settings()
        return cal.latest_finalized_session(self._now(), s.daily_data_ready_hour_et, s.daily_data_ready_minute_et)

    def daily_bars(self, symbol, start, end):
        end = min(end, self._last_session())
        path = _demo_path(symbol, end)
        return [b for b in path["bars"] if start <= b.session <= end]

    def corporate_actions(self, symbol, start, end):
        end = min(end, self._last_session())
        return [a for a in _demo_path(symbol, end)["actions"] if start <= a.ex_date <= end]

    def earnings_events(self, symbol):
        end = self._last_session()
        return list(_demo_path(symbol, end)["events"])


@lru_cache(maxsize=256)
def _demo_path(symbol: str, end: date) -> dict:
    rng = random.Random(_seed("demo-v1", symbol))
    sessions = cal.sessions_between(DEMO_START, end)
    is_etf = symbol in DEMO_ETFS or symbol == DEMO_BENCH
    idx = int(symbol[3:]) if symbol[3:].isdigit() else 0
    price = 20 + rng.random() * 180
    if symbol == DEMO_ILLIQUID:
        price = 3.5
    beta = 1.0 if symbol == DEMO_BENCH else (0.8 + rng.random() * 0.6)
    drift = 0.0 if is_etf else rng.uniform(-0.00025, 0.00015)
    idio_vol = 0.004 if is_etf else 0.012 + rng.random() * 0.012
    reversal = 0.18 if (idx % 3 == 0 and not is_etf) else 0.0  # synthetic short-term reversal effect
    momentum = 0.08 if (idx % 3 == 1 and not is_etf) else 0.0
    base_shares = 30_000 if symbol == DEMO_ILLIQUID else (1_500_000 + rng.random() * 4_000_000)
    earn_offset = rng.randint(0, 62)
    bars, actions, events = [], [], []
    prev_idio = 0.0
    drift_boost: list[float] = []
    split_mult = 1.0
    halted_until = None
    for i, d in enumerate(sessions):
        mrng = random.Random(_seed("demo-mkt-v1", d.isoformat()))
        mkt = mrng.gauss(0.00028, 0.010)
        if d.year == 2020 and d.month == 3:
            mkt += mrng.gauss(-0.006, 0.03)  # synthetic stress regime
        if d.year == 2022 and d.month in (5, 6, 9):
            mkt += -0.002
        eps = rng.gauss(0, idio_vol)
        idio = eps - reversal * prev_idio + momentum * prev_idio
        prev_idio = idio
        gap = 0.0
        is_earn = (not is_etf) and ((i + earn_offset) % 63 == 0) and i > 30
        if is_earn:
            surprise = rng.gauss(0.02, 0.10)
            gap = max(-0.25, min(0.25, surprise * 0.35))
            drift_boost = [surprise * 0.0035] * 20
            ev_known = datetime.combine(d, time(8, 0), cal.ET).astimezone(timezone.utc)  # pre-market release
            events.append(EventRecord("earnings", d, ev_known, {
                "surprise_pct": round(surprise, 6), "report_time": "pre-market", "synthetic": True}))
        boost = drift_boost.pop(0) if drift_boost and not is_earn else 0.0
        r = drift + beta * mkt + idio + boost
        prev_close = price
        open_ = prev_close * (1 + gap + rng.gauss(0, idio_vol * 0.25))
        price = max(0.5, open_ * (1 + r - gap * 0.2))
        hi = max(open_, price) * (1 + abs(rng.gauss(0, idio_vol * 0.4)))
        lo = min(open_, price) * (1 - abs(rng.gauss(0, idio_vol * 0.4)))
        vol = base_shares * (1 + abs(rng.gauss(0, 0.3))) * (2.5 if is_earn else 1.0)
        # dividends (raw prices drop by the dividend on ex-date)
        if symbol in DEMO_DIVIDENDS and i % 63 == 40:
            amt = round(prev_close * DEMO_DIVIDENDS[symbol] / split_mult, 4)
            actions.append(ActionRecord("dividend", d, amount=amt, pay_date=cal.add_sessions(d, 10),
                                        known_at=_close_ts(cal.prev_session(d)),
                                        payload={"synthetic": True}))
            open_ -= amt * split_mult
            price -= amt * split_mult
        if symbol == DEMO_SPLIT[0] and d >= DEMO_SPLIT[1] and split_mult == 1.0:
            split_mult = DEMO_SPLIT[2]
            actions.append(ActionRecord("split", d, ratio=DEMO_SPLIT[2], known_at=_close_ts(cal.prev_session(d)),
                                        payload={"synthetic": True}))
        if symbol == DEMO_DELIST[0] and d > DEMO_DELIST[1]:
            if not any(a.type == "delisting" for a in actions):
                actions.append(ActionRecord("delisting", d, known_at=_close_ts(DEMO_DELIST[1]),
                                            payload={"synthetic": True, "last_session": DEMO_DELIST[1].isoformat()}))
            continue
        if symbol == DEMO_HALT[0]:
            if d >= DEMO_HALT[1] and halted_until is None:
                halted_until = cal.add_sessions(d, DEMO_HALT[2])
            if halted_until and DEMO_HALT[1] <= d < halted_until:
                continue
        m = split_mult
        bars.append(BarRecord(d, round(open_ / m, 4), round(max(hi, open_, price) / m, 4),
                              round(min(lo, open_, price) / m, 4), round(price / m, 4),
                              round(vol * m), _close_ts(d), "synthetic"))
    return {"bars": bars, "actions": actions, "events": events}


# --------------------------------------------------------------------------------------------
# Live adapters (real sourced observations; paper trading only)
# --------------------------------------------------------------------------------------------
class TiingoProvider(MarketDataProvider):
    """Tiingo EOD. Raw OHLCV plus divCash/splitFactor (so dividends/splits are explicit events).

    Free tier (verify current terms): ~1,000 requests/day, 500 unique symbols/month, EOD history.
    """
    name = "tiingo"
    adjustment_basis = "raw"
    capabilities = {"bars": True, "corporate_actions": True, "earnings_events": False,
                    "delay": "end-of-day (finalized after close)"}
    BASE = "https://api.tiingo.com/tiingo/daily"

    def configured(self):
        return (bool(get_settings().tiingo_api_key), "TIINGO_API_KEY not set")

    def _h(self):
        return {"Authorization": f"Token {get_settings().tiingo_api_key}", "Content-Type": "application/json"}

    def _rows(self, symbol, start, end):
        r = get(f"{self.BASE}/{symbol.lower()}/prices", params={"startDate": start.isoformat(), "endDate": end.isoformat(),
                                                                 "format": "json", "resampleFreq": "daily"}, headers=self._h())
        return r.json()

    def daily_bars(self, symbol, start, end):
        out = []
        for row in self._rows(symbol, start, end):
            d = date.fromisoformat(row["date"][:10])
            if not cal.is_session(d):
                continue
            out.append(BarRecord(d, row["open"], row["high"], row["low"], row["close"], row["volume"], _close_ts(d)))
        return out

    def corporate_actions(self, symbol, start, end):
        out = []
        for row in self._rows(symbol, start, end):
            d = date.fromisoformat(row["date"][:10])
            if row.get("divCash"):
                out.append(ActionRecord("dividend", d, amount=float(row["divCash"]), known_at=_close_ts(d),
                                        payload={"source": "tiingo divCash on ex-date"}))
            sf = row.get("splitFactor")
            if sf and float(sf) != 1.0:
                out.append(ActionRecord("split", d, ratio=float(sf), known_at=_close_ts(d), payload={"source": "tiingo"}))
        return out

    def instruments(self, symbols):
        out = []
        for s in symbols:
            out.append(InstrumentMeta(s, "etf" if s in KNOWN_ETFS else "stock", None, LIVE_SECTORS.get(s), None))
        return out


class AlphaVantageProvider(MarketDataProvider):
    """Alpha Vantage. TIME_SERIES_DAILY is raw (unadjusted); compact = last 100 sessions on the free tier.

    Free tier (verify): 25 requests/day — far too few for a 40-symbol universe; use a paid plan or Tiingo.
    EARNINGS endpoint provides reported dates and surprises; consensus estimates are the provider's
    latest record and are NOT certified point-in-time, so event backtests are labeled limited.
    """
    name = "alphavantage"
    adjustment_basis = "raw"
    capabilities = {"bars": True, "corporate_actions": True, "earnings_events": True,
                    "delay": "end-of-day", "event_point_in_time": False}
    BASE = "https://www.alphavantage.co/query"

    def configured(self):
        return (bool(get_settings().alphavantage_api_key), "ALPHAVANTAGE_API_KEY not set")

    def _q(self, **params):
        params["apikey"] = get_settings().alphavantage_api_key
        data = get(self.BASE, params=params).json()
        for k in ("Note", "Information", "Error Message"):
            if k in data:
                raise ProviderError(f"Alpha Vantage: {str(data[k])[:200]}", retryable=(k != "Error Message"))
        return data

    def daily_bars(self, symbol, start, end):
        size = "full" if (end - start).days > 140 else "compact"
        data = self._q(function="TIME_SERIES_DAILY", symbol=symbol, outputsize=size)
        series = data.get("Time Series (Daily)", {})
        out = []
        for ds, row in series.items():
            d = date.fromisoformat(ds)
            if start <= d <= end and cal.is_session(d):
                out.append(BarRecord(d, float(row["1. open"]), float(row["2. high"]), float(row["3. low"]),
                                     float(row["4. close"]), float(row["5. volume"]), _close_ts(d)))
        return sorted(out, key=lambda b: b.session)

    def corporate_actions(self, symbol, start, end):
        out = []
        for row in self._q(function="DIVIDENDS", symbol=symbol).get("data", []):
            try:
                ex = date.fromisoformat(row["ex_dividend_date"])
            except (KeyError, ValueError):
                continue
            if start <= ex <= end:
                decl = row.get("declaration_date")
                known = _close_ts(date.fromisoformat(decl)) if decl and decl != "None" else _close_ts(cal.prev_session(ex))
                pay = row.get("payment_date")
                out.append(ActionRecord("dividend", ex, amount=float(row["amount"]), known_at=known,
                                        pay_date=date.fromisoformat(pay) if pay and pay != "None" else None))
        for row in self._q(function="SPLITS", symbol=symbol).get("data", []):
            try:
                ex = date.fromisoformat(row["effective_date"])
            except (KeyError, ValueError):
                continue
            if start <= ex <= end:
                out.append(ActionRecord("split", ex, ratio=float(row["split_factor"]), known_at=_close_ts(cal.prev_session(ex))))
        return out

    def earnings_events(self, symbol):
        out = []
        for row in self._q(function="EARNINGS", symbol=symbol).get("quarterlyEarnings", []):
            try:
                d = date.fromisoformat(row["reportedDate"])
                sp = row.get("surprisePercentage")
                surprise = float(sp) / 100 if sp not in (None, "None", "") else None
            except (KeyError, ValueError):
                continue
            rt = (row.get("reportTime") or "").lower()
            # Conservative availability: pre-market releases are usable from that session's close;
            # post-market/unknown releases only from the next session (known 09:00 ET next day).
            if rt.startswith("pre") and cal.is_session(d):
                event_day = d
            else:
                event_day = cal.next_session(d)
            known = datetime.combine(event_day, time(9, 0), cal.ET).astimezone(timezone.utc)
            out.append(EventRecord("earnings", event_day, known, {
                "reported_date": row["reportedDate"], "report_time": rt or "unknown",
                "reported_eps": row.get("reportedEPS"), "estimated_eps": row.get("estimatedEPS"),
                "surprise_pct": surprise, "point_in_time_estimate": False}))
        return out

    def instruments(self, symbols):
        return [InstrumentMeta(s, "etf" if s in KNOWN_ETFS else "stock", None, LIVE_SECTORS.get(s), None) for s in symbols]


class StooqProvider(MarketDataProvider):
    """Stooq daily CSV (no key). Prices are provider-adjusted for splits and dividends, therefore
    dividends are NOT credited separately (avoids double counting) and performance is labeled
    'provider-adjusted'. Availability is unreliable for automated clients (connection resets observed)."""
    name = "stooq"
    adjustment_basis = "split_dividend_adjusted"
    capabilities = {"bars": True, "corporate_actions": False, "earnings_events": False, "delay": "end-of-day"}

    def daily_bars(self, symbol, start, end):
        r = get("https://stooq.com/q/d/l/", params={"s": f"{symbol.lower()}.us", "i": "d"})
        out = []
        for row in csv.DictReader(io.StringIO(r.text)):
            try:
                d = date.fromisoformat(row["Date"])
            except (KeyError, ValueError):
                continue
            if start <= d <= end and cal.is_session(d):
                out.append(BarRecord(d, float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"]),
                                     float(row.get("Volume") or 0), _close_ts(d)))
        return out

    def instruments(self, symbols):
        return [InstrumentMeta(s, "etf" if s in KNOWN_ETFS else "stock", None, LIVE_SECTORS.get(s), None) for s in symbols]


_override: MarketDataProvider | None = None


def set_provider_override(p: MarketDataProvider | None) -> None:
    """Testing hook: inject a fixture provider."""
    global _override
    _override = p


def get_market_provider() -> MarketDataProvider:
    if _override is not None:
        return _override
    s = get_settings()
    if s.is_demo:
        return DemoMarketProvider()
    return {"tiingo": TiingoProvider, "alphavantage": AlphaVantageProvider, "stooq": StooqProvider}.get(
        s.market_provider, TiingoProvider)()


def annualize_note() -> str:
    return f"Sharpe uses daily returns, rf=0 (declared), annualized by sqrt(252)≈{math.sqrt(252):.2f}"


def history_start(end: date, years: int = 5) -> date:
    return end - timedelta(days=int(365.25 * years) + 10)
