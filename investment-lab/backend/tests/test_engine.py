"""Acceptance tests AC1, AC3-AC6, AC9, AC11 against PostgreSQL with deterministic fixtures."""
import copy
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from lab import market_calendar as cal
from lab.db import SessionLocal
from lab.models import (
    BenchmarkAccount, CorporateAction, Experiment, Instrument, Job, LedgerEntry, Lesson, MarketBar, Notification, Order,
    Portfolio, PositionLot, Proposal, Strategy, StrategyVersion, ValuationSnapshot,
)
from lab.services.engine import run_daily
from lab.services.portfolios import cash_balance, close_experiment, portfolio_state, reconcile, set_pause
from lab.services.proposals import DecisionError, create_proposal, decide
from lab.strategy_schema import StrategySpec

from .conftest import SIMPLE_SPEC, add_bars, after_close, during_session, inst, sessions

S = sessions(date(2026, 1, 5), 25)  # Mon 2026-01-05 ...
HIST = [d for d in sessions(date(2025, 11, 20), 40) if d < S[0]][-22:]  # liquidity/warmup history


def portfolio(db, user, family="contrarian") -> Portfolio:
    return db.scalars(select(Portfolio).where(Portfolio.user_id == user.id, Portfolio.family == family)).one()


def make_proposal(db, user, family="contrarian", spec=None, budget=Decimal(3000), expires_in=timedelta(days=7)) -> Proposal:
    spec = StrategySpec.model_validate(spec or SIMPLE_SPEC)
    st = Strategy(user_id=user.id, family=spec.family.value, name=spec.name, fingerprint=uuid.uuid4().hex, thesis_fingerprint="t")
    db.add(st)
    db.flush()
    v = StrategyVersion(user_id=user.id, strategy_id=st.id, version=1, rule_json=spec.canonical(), rule_hash=spec.rule_hash(),
                        thesis=spec.hypothesis, hypothesis={}, evidence_ids=[], generated_by="test")
    db.add(v)
    db.flush()
    pr = create_proposal(db, user.id, v, portfolio(db, user, family), score=80, components={}, gates={}, rationale={}, uncertainty={},
                         card={"name": spec.name}, budget=budget, exploratory=False, research_run_id=None, backtest_run_id=None,
                         critique_id=None)
    pr.expires_at = datetime.now(timezone.utc) + expires_in
    db.commit()
    return pr


def approve(db, user, pr, at) -> dict:
    r = decide(db, user.id, pr.id, "yes", uuid.uuid4().hex, pr.scope_hash, now=at)
    db.commit()
    return r


def flat(symbol_rows, start_px=100.0):
    return [(d, start_px, start_px) for d in symbol_rows]


def seed_basic(db, aaa_rows):
    """AAA follows `aaa_rows`; BBB/CCC flat at 50; benchmark flat at 400. 22 sessions of flat history first."""
    add_bars(db, "AAA", flat(HIST, aaa_rows[0][1]) + aaa_rows)
    for sym, px in (("BBB", 50), ("CCC", 60), ("BM", 400)):
        add_bars(db, sym, flat(HIST, px))
    days = [r[0] for r in aaa_rows]
    add_bars(db, "BBB", flat(days, 50))
    add_bars(db, "CCC", flat(days, 60))
    add_bars(db, "BM", flat(days, 400))
    db.commit()


def day(db, user, d, **kw):
    r = run_daily(db, user.id, d, now=kw.pop("now", after_close(d)), **kw)
    db.commit()
    return r


# ---------------------------------------------------------------- AC1
def test_ac1_five_independent_portfolios(db, user):
    ports = db.scalars(select(Portfolio).where(Portfolio.user_id == user.id)).all()
    assert sorted(p.family for p in ports) == ["ai_generated", "asymmetric", "contrarian", "event_driven", "growth"]
    for p in ports:
        assert cash_balance(db, p.id) == Decimal(10000)
        assert db.scalars(select(BenchmarkAccount).where(BenchmarkAccount.portfolio_id == p.id)).one().starting_cash == Decimal(10000)
        assert db.scalar(select(func.count()).select_from(ValuationSnapshot).where(ValuationSnapshot.portfolio_id == p.id)) == 0
        assert db.scalar(select(func.count()).select_from(PositionLot).where(PositionLot.portfolio_id == p.id)) == 0


# ---------------------------------------------------------------- AC3 / AC4
def test_ac4_signal_fill_at_next_open_with_slippage(db, user):
    seed_basic(db, [(S[0], 100, 100), (S[1], 99, 94), (S[2], 95, 96), (S[3], 96, 97)])
    pr = make_proposal(db, user)
    res = approve(db, user, pr, during_session(S[0]))
    assert res["state"] == "approved" and "awaiting" in res["message"].lower()
    exp_id = res["experiment_id"]
    day(db, user, S[0])
    assert db.scalar(select(func.count()).select_from(Order)) == 0  # no signal, no order
    day(db, user, S[1])
    o = db.scalars(select(Order)).one()
    assert o.side == "buy" and o.status == "pending" and o.eligible_session == S[2] and o.reserved_cash > 0
    assert o.quantity == 10  # 10% of NAV / (94 * (1 + 0.031 buffer))
    day(db, user, S[2])
    db.refresh(o)
    assert o.status == "filled" and o.fill_session == S[2]
    assert o.fill_price == Decimal("95.095000")  # open 95 + 10 bps adverse
    assert o.fill_at == cal.session_open_utc(S[2]) and o.fill_at > o.requested_at - timedelta(days=1)
    p = portfolio(db, user)
    assert cash_balance(db, p.id) == Decimal(10000) - Decimal("950.95")
    lot = db.scalars(select(PositionLot)).one()
    assert lot.quantity == 10 and lot.experiment_id == exp_id
    assert db.scalars(select(Notification).where(Notification.type == "fill")).first().title.startswith("[PAPER TRADING]")
    assert reconcile(db, p)["ok"]
    # isolation: other portfolios untouched
    for fam in ("growth", "asymmetric", "event_driven", "ai_generated"):
        assert cash_balance(db, portfolio(db, user, fam).id) == Decimal(10000)


def test_ac3_reject_and_expired_never_create_orders(db, user):
    seed_basic(db, [(S[0], 100, 100), (S[1], 99, 94), (S[2], 95, 96)])
    pr = make_proposal(db, user)
    r = decide(db, user.id, pr.id, "no", "key-reject-1", pr.scope_hash, now=during_session(S[0]))
    db.commit()
    assert r["state"] == "rejected" and r["experiment_id"] is None
    pr2 = make_proposal(db, user, expires_in=timedelta(seconds=-1))
    with pytest.raises(DecisionError) as e:
        decide(db, user.id, pr2.id, "yes", "key-expired-1", pr2.scope_hash)
    db.commit()
    assert e.value.code == "expired"
    assert db.get(Proposal, pr2.id).state == "expired"
    for d in S[:3]:
        day(db, user, d)
    assert db.scalar(select(func.count()).select_from(Order)) == 0
    assert db.scalar(select(func.count()).select_from(Experiment)) == 0


def test_ac3_idempotent_and_conflicting_decisions(db, user):
    pr = make_proposal(db, user)
    a = decide(db, user.id, pr.id, "yes", "idem-key-0001", pr.scope_hash)
    db.commit()
    b = decide(db, user.id, pr.id, "yes", "idem-key-0001", pr.scope_hash)
    db.commit()
    c = decide(db, user.id, pr.id, "yes", "idem-key-0002", pr.scope_hash)
    db.commit()
    assert a["experiment_id"] == b["experiment_id"] == c["experiment_id"] and b["idempotent_replay"]
    assert db.scalar(select(func.count()).select_from(Experiment)) == 1
    with pytest.raises(DecisionError) as e:
        decide(db, user.id, pr.id, "no", "idem-key-0003", pr.scope_hash)
    assert e.value.code == "already_decided"
    pr2 = make_proposal(db, user)
    with pytest.raises(DecisionError) as e:
        decide(db, user.id, pr2.id, "yes", "idem-key-0004", "0" * 64)
    assert e.value.code == "scope_mismatch"


def test_ac3_approval_after_open_cannot_fill_at_that_open(db, user):
    # Signal-bearing close on S[1] would have existed before; approval comes DURING S[2] after its open.
    seed_basic(db, [(S[0], 100, 100), (S[1], 99, 94), (S[2], 95, 90), (S[3], 91, 92), (S[4], 92, 93)])
    pr = make_proposal(db, user)
    approved_at = during_session(S[2])
    approve(db, user, pr, approved_at)
    day(db, user, S[1], now=after_close(S[1]))  # runs (late) for S1 but S1 close precedes approval -> no entries
    assert db.scalar(select(func.count()).select_from(Order)) == 0
    day(db, user, S[2])  # S2 close (90, -4.3%) -> no signal by rule; ensure S2 open never used
    day(db, user, S[3])
    for o in db.scalars(select(Order)).all():
        assert o.fill_at is None or o.fill_at > approved_at


def test_no_data_means_no_fill_then_expiry(db, user):
    seed_basic(db, [(S[0], 100, 100), (S[1], 99, 94)])
    add_bars(db, "BBB", flat(S[2:6], 50))
    add_bars(db, "BM", flat(S[2:6], 400))
    db.commit()
    approve(db, user, make_proposal(db, user), during_session(S[0]))
    for d in S[:2]:
        day(db, user, d)
    o = db.scalars(select(Order)).one()
    for d in S[2:5]:  # AAA has no bars
        day(db, user, d)
    db.refresh(o)
    assert o.status == "expired" and o.fill_price is None and o.reserved_cash == 0
    assert "no data means no fill" in o.status_reason
    assert cash_balance(db, portfolio(db, user).id) == Decimal(10000)


# ---------------------------------------------------------------- AC5
def _open_position(db, user, aaa_tail):
    rows = [(S[0], 100, 100), (S[1], 99, 94), (S[2], 95, 96)] + aaa_tail
    seed_basic(db, rows)
    approve(db, user, make_proposal(db, user), during_session(S[0]))
    for d in S[:3]:
        day(db, user, d)
    return db.scalars(select(PositionLot)).one()


def test_ac5_exit_and_realized_pnl_reconcile(db, user):
    lot = _open_position(db, user, [(S[3], 96, 105), (S[4], 104, 104)])
    day(db, user, S[3])
    sell = db.scalars(select(Order).where(Order.side == "sell")).one()
    assert sell.eligible_session == S[4] and "return_since_entry" in sell.reason
    day(db, user, S[4])
    db.refresh(lot)
    assert lot.status == "closed" and lot.exit_price == Decimal("103.896000")
    assert lot.realized_pnl == Decimal("1038.960000") - Decimal("950.950000")
    p = portfolio(db, user)
    assert cash_balance(db, p.id) == Decimal(10000) + lot.realized_pnl
    assert reconcile(db, p)["ok"]


def test_ac5_gap_through_stop_is_not_guaranteed(db, user):
    lot = _open_position(db, user, [(S[3], 96, 85), (S[4], 70, 72)])
    day(db, user, S[3])
    day(db, user, S[4])
    db.refresh(lot)
    assert lot.status == "closed"
    loss = float(lot.exit_price / lot.entry_price - 1)
    assert loss < -0.25, "gap should produce a loss far beyond the -10% stop threshold"


def test_ac5_split_and_dividend(db, user):
    lot = _open_position(db, user, [(S[3], 48, 48.5), (S[4], 48.5, 49)])
    aaa = inst(db, "AAA")
    db.add(CorporateAction(instrument_id=aaa.id, type="split", ex_date=S[3], ratio=Decimal(2), known_at=cal.session_close_utc(S[2]), source="fixture"))
    db.add(CorporateAction(instrument_id=aaa.id, type="dividend", ex_date=S[4], amount=Decimal("0.50"), known_at=cal.session_close_utc(S[2]), source="fixture"))
    db.commit()
    cost = lot.cost_basis
    day(db, user, S[3])
    db.refresh(lot)
    assert lot.quantity == 20 and lot.entry_price == Decimal("47.547500") and lot.cost_basis == cost
    day(db, user, S[4])
    db.refresh(lot)
    assert lot.dividends == Decimal("10.000000")
    day(db, user, S[4])  # rerun is idempotent: no double split/dividend
    db.refresh(lot)
    assert lot.quantity == 20 and lot.dividends == Decimal("10.000000")
    p = portfolio(db, user)
    assert reconcile(db, p)["ok"]
    nav = portfolio_state(db, p, S[4])["nav"]
    assert nav == Decimal(10000) - Decimal("950.95") + Decimal(10) + 20 * Decimal(49)


def test_ac5_missing_quote_and_delisting_suspend(db, user):
    lot = _open_position(db, user, [])
    others = S[3:10]
    add_bars(db, "BBB", flat(others, 50))
    add_bars(db, "BM", flat(others, 400))
    db.commit()
    for d in others:
        day(db, user, d)
    db.refresh(lot)
    assert lot.status == "unresolved"  # no invented liquidation price
    st = portfolio_state(db, portfolio(db, user), others[-1])
    assert st["quality"] == "unresolved" and st["positions"][0]["last_price"] == Decimal("96")
    assert db.scalars(select(Notification).where(Notification.type == "stale_data")).first() is not None
    assert db.scalar(select(func.count()).select_from(Order).where(Order.side == "sell")) == 0


# ---------------------------------------------------------------- AC6
def test_ac6_concurrent_workers_do_not_double_fill(db, user):
    seed_basic(db, [(S[0], 100, 100), (S[1], 99, 94), (S[2], 95, 96)])
    approve(db, user, make_proposal(db, user), during_session(S[0]))
    day(db, user, S[0])
    day(db, user, S[1])
    day(db, user, S[1])  # duplicate signal run -> idempotent
    assert db.scalar(select(func.count()).select_from(Order)) == 1
    errors = []

    def worker():
        s = SessionLocal()
        try:
            run_daily(s, user.id, S[2], now=after_close(S[2]))
            s.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(e)
        finally:
            s.close()

    ts = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    assert db.scalar(select(func.count()).select_from(PositionLot)) == 1
    assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event_type == "buy")) == 1


def test_ac6_gap_up_fill_resized_never_negative_cash(db, user):
    seed_basic(db, [(S[0], 100, 100), (S[1], 99, 94), (S[2], 2000, 2000)])
    approve(db, user, make_proposal(db, user), during_session(S[0]))
    day(db, user, S[0])
    day(db, user, S[1])
    o = db.scalars(select(Order)).one()
    o.quantity = Decimal(200)  # simulate a reservation overwhelmed by a huge gap
    db.commit()
    day(db, user, S[2])
    db.refresh(o)
    assert o.status == "filled" and o.quantity == 4  # floor(10000 / 2002)
    assert cash_balance(db, portfolio(db, user).id) >= 0


def test_ac6_simultaneous_proposals_respect_risk_budget(db, user):
    prs = [make_proposal(db, user) for _ in range(4)]
    for pr in prs[:3]:
        approve(db, user, pr, during_session(S[0]))
    with pytest.raises(DecisionError) as e:
        decide(db, user.id, prs[3].id, "yes", "budget-key-4", prs[3].scope_hash)
    db.commit()
    assert e.value.code == "blocked" and db.get(Proposal, prs[3].id).state == "blocked"


def test_ac6_pause_blocks_entries_but_exits_continue(db, user):
    lot = _open_position(db, user, [(S[3], 96, 106), (S[4], 105, 105)])
    p = portfolio(db, user)
    set_pause(db, user.id, p, True, "test")
    db.commit()
    day(db, user, S[3])
    assert db.scalars(select(Order).where(Order.side == "sell")).one()  # exit continues
    day(db, user, S[4])
    db.refresh(lot)
    assert lot.status == "closed"


def test_ac6_pause_blocks_new_entries(db, user):
    seed_basic(db, [(S[0], 100, 100), (S[1], 99, 94), (S[2], 95, 96)])
    approve(db, user, make_proposal(db, user), during_session(S[0]))
    set_pause(db, user.id, portfolio(db, user), True, user.id, "test pause")
    db.commit()
    day(db, user, S[0])
    day(db, user, S[1])
    assert db.scalar(select(func.count()).select_from(Order)) == 0


def test_ac6_drawdown_circuit_breaker(db, user):
    seed_basic(db, [(S[0], 100, 100), (S[1], 99, 94), (S[2], 95, 96)])
    approve(db, user, make_proposal(db, user), during_session(S[0]))
    p = portfolio(db, user)
    p.peak_nav = Decimal(12000)  # current NAV 10,000 => -16.7% from peak
    db.commit()
    day(db, user, S[0])
    db.refresh(p)
    assert p.circuit_breaker
    day(db, user, S[1])
    assert db.scalar(select(func.count()).select_from(Order)) == 0
    assert db.scalars(select(Notification).where(Notification.type == "drawdown")).one().severity == "urgent"
    set_pause(db, user.id, p, False, user.id)  # explicit resume
    db.commit()
    assert not p.circuit_breaker


def test_user_close_experiment_sells_next_open(db, user):
    lot = _open_position(db, user, [(S[3], 96, 97), (S[4], 98, 98)])
    exp = db.get(Experiment, lot.experiment_id)
    close_experiment(db, user.id, exp, user.id)
    db.commit()
    day(db, user, S[3])
    day(db, user, S[4])
    db.refresh(lot)
    db.refresh(exp)
    assert lot.status == "closed" and lot.closed_session == S[4] and exp.status == "closed"


def test_benchmark_shadow_matched_inception(db, user):
    seed_basic(db, [(S[0], 100, 100), (S[1], 99, 99)])
    db.query(MarketBar).filter(MarketBar.instrument_id == inst(db, "BM").id, MarketBar.session == S[1]).update({"close": Decimal(440)})
    db.commit()
    day(db, user, S[0])
    day(db, user, S[1])
    b = db.scalars(select(BenchmarkAccount).join(Portfolio).where(Portfolio.family == "growth")).one()
    assert b.entry_session == S[0] and b.entry_price == Decimal("400.400000")
    snap = db.scalars(select(ValuationSnapshot).where(ValuationSnapshot.portfolio_id == b.portfolio_id, ValuationSnapshot.session == S[1])).one()
    assert float(snap.benchmark_nav) == pytest.approx(10000 / 400.4 * 440, rel=1e-6)
    assert snap.nav == Decimal(10000)


# ---------------------------------------------------------------- catch-up / jobs (AC11)
def test_catch_up_never_creates_retroactive_signals(db, user):
    seed_basic(db, [(S[0], 100, 100), (S[1], 99, 94), (S[2], 95, 96), (S[3], 96, 96)])
    approve(db, user, make_proposal(db, user), during_session(S[0]))
    late_now = after_close(S[3])  # worker was down; processing S1 three days late
    r = run_daily(db, user.id, S[1], now=late_now)
    db.commit()
    assert r["catch_up"] and not r["signals_generated"]
    assert db.scalar(select(func.count()).select_from(Order)) == 0


def test_scheduler_idempotent_and_lease_recovery(db, user):
    from lab.jobs import claim, schedule_tick

    now = after_close(S[3])
    a = schedule_tick(db, now)
    b = schedule_tick(db, now)
    assert a["created"] and not b["created"]
    keys = db.scalars(select(Job.schedule_key).where(Job.type == "daily")).all()
    assert len(keys) == len(set(keys))
    j = claim(db, "worker-a", lease_seconds=1)
    assert j is not None and j.state == "running"
    j.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)  # simulate crash
    db.commit()
    j2 = claim(db, "worker-b")
    assert j2 is not None


def test_job_failure_retries_then_dead_letter(db, user):
    from lab.jobs import enqueue, run_job

    job, _ = enqueue(db, user.id, "backtest", "bt:missing", {"strategy_version_id": "does-not-exist"}, max_attempts=1)
    job.attempts = 1
    job.state = "running"
    db.commit()
    run_job(job.id)
    db.expire_all()
    j = db.get(Job, job.id)
    assert j.state == "dead" and j.error
    assert db.scalars(select(Notification).where(Notification.type == "job_failure")).first()


def test_notifications_deduplicated(db, user):
    from lab.services.core import notify

    assert notify(db, user.id, "x", "t", "b", "same-key")
    assert not notify(db, user.id, "x", "t", "b", "same-key")
    db.commit()
    assert db.scalar(select(func.count()).select_from(Notification).where(Notification.dedupe_key == "same-key")) == 1


def test_export_and_reset_archive(db, user):
    from lab.services.backtests import export_all, reset_lab

    lot = _open_position(db, user, [])
    data = export_all(db, user.id)
    assert len(data["portfolios"]) == 5 and any(e["event_type"] == "buy" for e in data["ledger"]) and data["orders"]
    new = reset_lab(db, user.id, user.id)
    db.commit()
    assert len(new) == 5 and all(p.generation == 2 for p in new)
    assert db.scalar(select(func.count()).select_from(Portfolio).where(Portfolio.status == "archived")) == 5
    assert db.get(PositionLot, lot.id) is not None  # history preserved


# ---------------------------------------------------------------- AC9 learning / modification
def test_ac9_monthly_learning_and_versioned_modification(db, user):
    from lab.agents.learning import propose_modification, run_monthly

    lot = _open_position(db, user, [(S[3], 96, 97)])
    exp = db.get(Experiment, lot.experiment_id)
    res = run_monthly(db, user.id, "2026-01", use_llm=False)
    db.commit()
    les = db.scalars(select(Lesson)).one()
    assert les.outcome == "inconclusive" and les.recommendation == "continue" and "too few" in les.lesson
    assert res["lessons"][0]["outcome"] == "inconclusive"
    old_version = exp.approved_version_id
    spec = StrategySpec.model_validate(db.get(StrategyVersion, old_version).rule_json)
    new_data = copy.deepcopy(spec.canonical())
    new_data["exit"]["any"][0]["value"] = 0.5  # new version: much higher profit target
    pr = propose_modification(db, exp, spec, "test modification", new_data)
    db.commit()
    assert pr.kind == "modification" and pr.state == "pending" and pr.legacy_treatment == "original_rules_until_exit"
    db.refresh(exp)
    assert exp.status == "active" and exp.approved_version_id == old_version  # nothing changed before approval
    approve(db, user, pr, during_session(S[3]))
    db.refresh(exp)
    assert exp.status == "legacy" and not exp.entries_allowed
    nv = db.get(StrategyVersion, db.get(Proposal, pr.id).strategy_version_id)
    assert nv.parent_version_id == old_version and nv.diff
    # legacy lot keeps ORIGINAL rules: +10% target (old) triggers exit although the new version needs +50%
    add_bars(db, "AAA", [(S[4], 97, 106), (S[5], 106, 107)])
    add_bars(db, "BBB", flat(S[4:6], 50))
    add_bars(db, "BM", flat(S[4:6], 400))
    db.commit()
    day(db, user, S[4])
    sell = db.scalars(select(Order).where(Order.side == "sell")).one()
    assert sell.lot_id == lot.id and db.get(PositionLot, lot.id).strategy_version_id == old_version


def test_invalidation_rule_stops_entries(db, user):
    spec = copy.deepcopy(SIMPLE_SPEC)
    spec["thesis_invalidation"] = [{"metric": "experiment_return", "op": "lt", "value": -0.01, "min_closed_trades": 1}]
    seed_basic(db, [(S[0], 100, 100), (S[1], 99, 94), (S[2], 95, 96), (S[3], 96, 80), (S[4], 70, 70)])
    approve(db, user, make_proposal(db, user, spec=spec), during_session(S[0]))
    for d in S[:5]:
        day(db, user, d)
    exp = db.scalars(select(Experiment)).one()
    assert exp.thesis_status == "invalidated" and not exp.entries_allowed
    assert db.scalars(select(Notification).where(Notification.type == "thesis")).first()


def test_tenant_isolation_in_services(db, user):
    from lab.models import User
    from lab.services.portfolios import init_portfolios

    other = User(email="other@example.com", password_hash="x")
    db.add(other)
    db.flush()
    init_portfolios(db, other.id)
    db.commit()
    pr = make_proposal(db, user)
    with pytest.raises(DecisionError) as e:
        decide(db, other.id, pr.id, "yes", "cross-tenant-1", pr.scope_hash)
    assert e.value.status == 404
    assert db.scalar(select(func.count()).select_from(Instrument)) >= 4
