"""Pure-logic tests: calendar, schema validation, indicators, risk, execution, URL safety, sanitisation."""
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from lab import market_calendar as cal
from lab.agents.templates import TEMPLATES
from lab.execution import fill_price, whole_shares
from lab.indicators import Bar, BarView, EarningsEvent, LookAheadError, PositionContext, compute, rsi_wilder
from lab.providers.http import UnsafeURLError, assert_safe_url, is_safe_display_url
from lab.providers.research import sanitize_text
from lab.risk import EntryContext, check_entry
from lab.rules import evaluate
from lab.strategy_schema import Indicator, StrategySpec, validate_spec

from .conftest import SIMPLE_SPEC


# ---------------------------------------------------------------- calendar
def test_holidays_2026():
    for d in [date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
              date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)]:
        assert not cal.is_session(d), d
    assert cal.is_session(date(2026, 11, 27))
    assert date(2026, 11, 27) in cal.early_closes(2026)
    assert cal.session_close_utc(date(2026, 11, 27)).hour == 18  # 13:00 EST


def test_new_year_saturday_not_observed_friday():
    # Jan 1 2022 was Saturday: Dec 31 2021 was a normal session
    assert cal.is_session(date(2021, 12, 31))


def test_dst_open_times():
    assert cal.session_open_utc(date(2026, 7, 1)).hour == 13  # EDT
    assert cal.session_open_utc(date(2026, 12, 1)).hour == 14  # EST


def test_first_open_after_never_uses_past_open():
    d = date(2026, 12, 1)
    approved = datetime(2026, 12, 1, 15, 0, tzinfo=timezone.utc)  # 10:00 ET, after the open
    assert cal.first_open_after(approved) == date(2026, 12, 2)
    assert cal.first_open_after(datetime(2026, 12, 1, 13, 0, tzinfo=timezone.utc)) == d  # before open
    assert cal.first_session_with_close_after(approved) == d


# ---------------------------------------------------------------- schema
def test_all_templates_validate_and_hash_stable():
    for k, t in TEMPLATES.items():
        s1, s2 = StrategySpec.model_validate(t), StrategySpec.model_validate(t)
        assert s1.rule_hash() == s2.rule_hash()
        assert len(s1.rule_hash()) == 64


def _spec(**patch):
    import copy

    d = copy.deepcopy(SIMPLE_SPEC)
    d.update(patch)
    return d


def test_vague_rule_rejected():
    with pytest.raises(ValidationError):
        validate_spec(_spec(entry={"all": [{"indicator": "undervalued", "op": "gte", "value": 1}]}))


def test_lookahead_lag_rejected():
    with pytest.raises(ValidationError, match="look-ahead"):
        validate_spec(_spec(entry={"all": [{"indicator": "return_nd", "params": {"n": 5}, "op": "gte", "value": 0.0, "lag": -1}]}))


def test_exit_requires_time_limit_and_ranges():
    with pytest.raises(ValidationError, match="sessions_held"):
        validate_spec(_spec(exit={"any": [{"indicator": "return_since_entry", "op": "gte", "value": 0.1}]}))
    with pytest.raises(ValidationError):
        validate_spec(_spec(entry={"all": [{"indicator": "rsi", "params": {"n": 14}, "op": "lte", "value": 150}]}))
    with pytest.raises(ValidationError):
        validate_spec(_spec(entry={"all": [{"indicator": "return_nd", "params": {"n": 5000}, "op": "lte", "value": 0}]}))
    with pytest.raises(ValidationError, match="position-level"):
        validate_spec(_spec(entry={"all": [{"indicator": "sessions_held", "op": "gte", "value": 1}]}))


def test_extra_fields_and_code_injection_rejected():
    with pytest.raises(ValidationError):
        validate_spec(_spec(code="__import__('os').system('rm -rf /')"))
    with pytest.raises(ValidationError):
        validate_spec(_spec(entry={"all": [{"indicator": "close", "op": "gte", "value": 1, "sql": "DROP TABLE"}]}))


def test_binary_and_asymmetric_caps():
    with pytest.raises(ValidationError, match="5%"):
        validate_spec(_spec(family="event_driven", catalyst={"type": "earnings", "binary": True},
                            allocation={"max_experiment_fraction": 0.2, "max_position_fraction": 0.08, "max_positions": 2}))
    with pytest.raises(ValidationError, match="asymmetric"):
        validate_spec(_spec(family="asymmetric"))


def test_event_indicator_requires_dataset():
    with pytest.raises(ValidationError, match="earnings_events"):
        validate_spec(_spec(entry={"all": [{"indicator": "earnings_surprise_pct", "params": {"within_sessions": 3}, "op": "gte", "value": 0.05}]}))


# ---------------------------------------------------------------- indicators
def _bars(closes, start=date(2026, 1, 2)):
    out, d = [], start
    for c in closes:
        out.append(Bar(d, c, c * 1.01, c * 0.99, c, 1_000_000))
        d = cal.next_session(d)
    return out


def test_indicator_values():
    bars = _bars([100, 101, 102, 103, 104, 110])
    v = BarView(bars, bars[-1].session)
    assert compute(Indicator.return_nd, {"n": 5}, v) == pytest.approx(0.10)
    assert compute(Indicator.sma_ratio, {"n": 5}, v) == pytest.approx(110 / 104 - 1)
    assert compute(Indicator.return_nd, {"n": 6}, v) is None  # insufficient warmup -> None, never fabricated
    assert compute(Indicator.avg_dollar_volume, {"n": 5}, v) == pytest.approx(sum([101, 102, 103, 104, 110]) * 1e6 / 5)
    vol = compute(Indicator.volatility, {"n": 5}, v)
    rets = [math.log(b / a) for a, b in zip([100, 101, 102, 103, 104], [101, 102, 103, 104, 110])]
    m = sum(rets) / 5
    assert vol == pytest.approx(math.sqrt(sum((r - m) ** 2 for r in rets) / 4) * math.sqrt(252))


def test_rsi_extremes():
    assert rsi_wilder([float(i) for i in range(1, 50)], 14) == 100.0
    assert 0 <= rsi_wilder([100 + ((-1) ** i) * i * 0.1 for i in range(60)], 14) <= 100


def test_lookahead_view_rejected():
    bars = _bars([1, 2, 3])
    with pytest.raises(LookAheadError):
        BarView(bars, bars[1].session)
    ev = EarningsEvent(bars[2].session, datetime(2030, 1, 1, tzinfo=timezone.utc), 0.1)
    with pytest.raises(LookAheadError):
        BarView(bars, bars[-1].session, datetime(2026, 1, 10, tzinfo=timezone.utc), [ev])


def test_position_indicators_and_missing():
    bars = _bars([100, 89])
    v = BarView(bars, bars[-1].session)
    pos = PositionContext(100.0, bars[0].session, 1, 105.0)
    assert compute(Indicator.return_since_entry, {}, v, pos) == pytest.approx(-0.11)
    assert compute(Indicator.drawdown_from_peak, {}, v, pos) == pytest.approx(89 / 105 - 1)
    spec = StrategySpec.model_validate(SIMPLE_SPEC)
    ok, trace = evaluate(spec.exit, v, pos)
    assert ok and trace["result"]
    ok, trace = evaluate(StrategySpec.model_validate(_spec(entry={"all": [{"indicator": "return_nd", "params": {"n": 20}, "op": "lte", "value": 0}]})).entry, v)
    assert not ok and trace["all"][0]["missing"]


# ---------------------------------------------------------------- execution & risk
def test_fill_price_and_sizing():
    assert fill_price(Decimal("100"), "buy", Decimal("10")) == Decimal("100.100000")
    assert fill_price(Decimal("100"), "sell", Decimal("10")) == Decimal("99.900000")
    assert whole_shares(Decimal("1000"), Decimal("33.4")) == Decimal(29)
    assert whole_shares(Decimal("10"), Decimal("33.4")) == 0


def _ctx(**kw):
    base = dict(nav=Decimal(10000), cash=Decimal(10000), reserved=Decimal(0), ticker_exposure=Decimal(0), sector_exposure=Decimal(0),
                experiment_committed=Decimal(0), experiment_budget=Decimal(3000), instrument_type="stock", price=50.0,
                avg_dollar_volume_20d=1e8, bar_age_sessions=0, portfolio_paused=False, circuit_breaker=False,
                experiment_entries_allowed=True, global_paused=False, binary_catalyst=False, strategy_max_position_fraction=0.10,
                strategy_max_experiment_fraction=0.30, strategy_min_price=5, strategy_min_adv=0)
    base.update(kw)
    return EntryContext(**base)


def test_risk_limits():
    d = check_entry(_ctx(), {})
    assert d.allowed and d.max_notional == Decimal(1000)  # 10% per ticker
    assert not check_entry(_ctx(price=3.0), {}).allowed  # min price
    assert not check_entry(_ctx(avg_dollar_volume_20d=1e6), {}).allowed  # liquidity
    assert not check_entry(_ctx(bar_age_sessions=3), {}).allowed  # stale quote
    assert not check_entry(_ctx(price=None, bar_age_sessions=None), {}).allowed
    assert not check_entry(_ctx(circuit_breaker=True), {}).allowed
    assert not check_entry(_ctx(global_paused=True), {}).allowed
    assert check_entry(_ctx(binary_catalyst=True), {}).max_notional == Decimal(500)  # 5% binary
    assert check_entry(_ctx(sector_exposure=Decimal(1800)), {}).max_notional == Decimal(200)  # 20% sector
    assert check_entry(_ctx(ticker_exposure=Decimal(900)), {}).max_notional == Decimal(100)  # consolidated ticker
    assert check_entry(_ctx(experiment_committed=Decimal(2800)), {}).max_notional == Decimal(200)  # budget
    d = check_entry(_ctx(cash=Decimal(1500)), {})  # min cash 10% of NAV
    assert d.max_notional == Decimal(500) and d.binding_limit == "min_cash"
    assert not check_entry(_ctx(cash=Decimal(900)), {}).allowed
    assert check_entry(_ctx(instrument_type="etf", sector_exposure=None, strategy_max_position_fraction=0.25), {}).max_notional == Decimal(2500)
    # stricter approved limit prevails
    assert check_entry(_ctx(strategy_max_position_fraction=0.05), {}).max_notional == Decimal(500)


# ---------------------------------------------------------------- security
@pytest.mark.parametrize("url", ["http://api.openalex.org/x", "https://169.254.169.254/latest/meta-data", "https://127.0.0.1/",
                                 "https://10.0.0.1/", "https://user:pw@api.openalex.org/", "https://evil.example.com/",
                                 "javascript:alert(1)", "file:///etc/passwd", "https://[::1]/"])
def test_unsafe_urls_rejected(url):
    with pytest.raises(UnsafeURLError):
        assert_safe_url(url, resolve=False)


def test_display_url_filter():
    assert is_safe_display_url("https://doi.org/10.1086/422627")
    assert not is_safe_display_url("javascript:alert(1)")
    assert not is_safe_display_url("https://192.168.1.1/admin")


def test_sanitize_strips_html_and_controls():
    s = sanitize_text("<script>alert(1)</script><b>Momentum</b>\x00 works &amp; persists" + "x" * 5000)
    assert "<" not in s and "\x00" not in s and "&amp;" not in s and len(s) <= 4000


def test_lifetime():
    assert cal.add_sessions(date(2026, 1, 2), 252) > date(2026, 12, 1)
    assert timedelta(days=1)
