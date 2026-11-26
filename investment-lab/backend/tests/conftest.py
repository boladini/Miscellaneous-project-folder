import os

os.environ.setdefault("DATABASE_URL", os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://lab:lab@localhost:5432/lab_test"))
os.environ["LAB_MODE"] = "demo"
os.environ["LLM_PROVIDER"] = "template"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["SECRET_KEY"] = "test-secret-key-test-secret-key-0123456789"
os.environ["ADMIN_EMAIL"] = "owner@example.com"
os.environ["ADMIN_PASSWORD"] = "pw-owner-123"
os.environ["CRON_SECRET"] = "cron-test-secret"

from datetime import date, datetime, time, timedelta, timezone  # noqa: E402
from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from lab import market_calendar as cal  # noqa: E402
from lab.db import Base, SessionLocal, get_engine  # noqa: E402
from lab.models import Instrument, MarketBar, User  # noqa: E402
from lab.providers import llm as llm_mod  # noqa: E402
from lab.providers import market as market_mod  # noqa: E402
from lab.providers.market import InstrumentMeta, MarketDataProvider  # noqa: E402


class FixtureProvider(MarketDataProvider):
    """Test market provider: instruments only; bars are inserted directly by tests."""
    name = "fixture"
    adjustment_basis = "raw"
    synthetic = True
    capabilities = {"bars": True}

    def __init__(self, symbols=("AAA", "BBB", "CCC", "ETF1"), bench="BM"):
        self.symbols, self.bench = list(symbols), bench

    def universe(self):
        return self.symbols

    def benchmark(self):
        return self.bench

    def instruments(self, symbols):
        return [InstrumentMeta(s, "etf" if s.startswith("ETF") or s == self.bench else "stock",
                               sector=("Tech" if s in ("AAA", "BBB") else "Health")) for s in symbols]

    def daily_bars(self, symbol, start, end):
        return []


@pytest.fixture(scope="session")
def engine():
    eng = get_engine()
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def db(engine):
    with engine.begin() as c:
        names = ", ".join(t.name for t in Base.metadata.sorted_tables)
        c.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    market_mod.set_provider_override(FixtureProvider())
    llm_mod.set_llm_override(None)
    s = SessionLocal()
    yield s
    s.close()
    market_mod.set_provider_override(None)
    llm_mod.set_llm_override(None)


@pytest.fixture()
def user(db):
    from lab.bootstrap import hash_password
    from lab.services.market_data import ensure_instruments
    from lab.services.portfolios import init_portfolios

    u = User(email="owner@example.com", password_hash=hash_password("pw-owner-123"))
    db.add(u)
    db.flush()
    init_portfolios(db, u.id)
    ensure_instruments(db)
    # backdate inception so tests can run historical sessions
    from lab.models import BenchmarkAccount, Portfolio

    for p in db.query(Portfolio).all():
        p.inception_at = datetime(2025, 12, 31, tzinfo=timezone.utc)
    for b in db.query(BenchmarkAccount).all():
        b.inception_at = datetime(2025, 12, 31, tzinfo=timezone.utc)
    db.commit()
    return u


def inst(db, symbol) -> Instrument:
    return db.query(Instrument).filter_by(symbol=symbol, namespace="demo").one()


def add_bars(db, symbol, rows):
    """rows: list of (session, open, close) or (session, o, h, l, c, v)."""
    i = inst(db, symbol)
    for r in rows:
        if len(r) == 3:
            d, o, c = r
            h, lo, v = max(o, c) * 1.01, min(o, c) * 0.99, 2_000_000
        else:
            d, o, h, lo, c, v = r
        db.add(MarketBar(instrument_id=i.id, session=d, open=Decimal(str(o)), high=Decimal(str(h)), low=Decimal(str(lo)),
                         close=Decimal(str(c)), volume=Decimal(str(v)), provider="fixture",
                         observed_at=cal.session_close_utc(d), adjustment_basis="raw", quality="ok"))
    db.flush()


def sessions(start: date, n: int) -> list[date]:
    out, d = [], start
    if not cal.is_session(d):
        d = cal.next_session(d)
    while len(out) < n:
        out.append(d)
        d = cal.next_session(d)
    return out


def after_close(d: date, hours: int = 1) -> datetime:
    return cal.data_ready_utc(d) + timedelta(hours=hours)


def during_session(d: date) -> datetime:
    return datetime.combine(d, time(11, 0), cal.ET).astimezone(timezone.utc)


SIMPLE_SPEC = {
    "schema_version": 1, "name": "Test dip buyer", "family": "contrarian", "horizon": "swing",
    "universe": {"asset_types": ["stock"], "market": "US", "min_price": 5, "min_avg_dollar_volume_20d": 0},
    "required_data": ["daily_ohlcv", "instrument_metadata"],
    "entry": {"all": [{"indicator": "return_nd", "params": {"n": 1}, "op": "lte", "value": -0.05}]},
    "ranking": {"indicator": "return_nd", "params": {"n": 1}, "direction": "ascending"},
    "allocation": {"max_experiment_fraction": 0.30, "max_position_fraction": 0.10, "max_positions": 2},
    "exit": {"any": [{"indicator": "return_since_entry", "op": "gte", "value": 0.10},
                     {"indicator": "return_since_entry", "op": "lte", "value": -0.10},
                     {"indicator": "sessions_held", "op": "gte", "value": 10}]},
    "holding": {"min_sessions": 0, "max_sessions": 10},
    "execution": {"slippage_bps": 10, "order_expiry_sessions": 3},
    "hypothesis": "Test hypothesis: one-day 5% drops rebound.",
    "prediction": {"metric": "avg_trade_return", "direction": "positive", "threshold": 0, "horizon_sessions": 10},
}


@pytest.fixture()
def fake_llm():
    def _make(scripts):
        f = llm_mod.FakeLLM(scripts)
        llm_mod.set_llm_override(f)
        return f
    return _make
