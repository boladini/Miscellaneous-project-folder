"""Market data ingestion, storage and point-in-time dataset assembly."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .. import market_calendar as cal
from ..backtest import Dataset, InstrumentData
from ..config import get_settings
from ..indicators import Bar, EarningsEvent
from ..models import CorporateAction, Instrument, MarketBar, MarketEvent, utcnow
from ..providers.http import ProviderError
from ..providers.market import MarketDataProvider, NotSupported, get_market_provider, history_start
from .core import provider_event


def namespace() -> str:
    return "demo" if get_settings().is_demo else "live"


def ensure_instruments(s: Session, provider: MarketDataProvider | None = None) -> list[Instrument]:
    p = provider or get_market_provider()
    ns = namespace()
    syms = list(dict.fromkeys(p.universe() + [p.benchmark()]))
    existing = {i.symbol: i for i in s.scalars(select(Instrument).where(Instrument.namespace == ns)).all()}
    for meta in p.instruments([x for x in syms if x not in existing]):
        inst = Instrument(namespace=ns, symbol=meta.symbol, name=meta.name, exchange=meta.exchange, type=meta.type,
                          sector=meta.sector)
        s.add(inst)
        existing[meta.symbol] = inst
    s.flush()
    return [existing[x] for x in syms]


def instrument_by_symbol(s: Session, symbol: str) -> Instrument | None:
    return s.scalars(select(Instrument).where(Instrument.namespace == namespace(), Instrument.symbol == symbol)).first()


def last_bar_session(s: Session, instrument_id: str) -> date | None:
    return s.scalar(select(func.max(MarketBar.session)).where(MarketBar.instrument_id == instrument_id))


def ingest(s: Session, end: date | None = None, years: int = 5, provider: MarketDataProvider | None = None,
           symbols: list[str] | None = None, include_actions: bool = True) -> dict:
    """Fetch missing bars/actions/events through `end`. Never mixes adjustment conventions silently."""
    p = provider or get_market_provider()
    cfg = get_settings()
    ok_cfg, why = p.configured()
    if not ok_cfg:
        provider_event(s, f"market:{p.name}", "market", False, why, calls=0, state="unconfigured")
        return {"status": "unconfigured", "reason": why, "inserted": 0}
    end = end or cal.latest_finalized_session(datetime.now(timezone.utc), cfg.daily_data_ready_hour_et,
                                              cfg.daily_data_ready_minute_et)
    insts = ensure_instruments(s, p)
    if symbols:
        insts = [i for i in insts if i.symbol in symbols]
    inserted, errors, mismatches = 0, [], []
    for inst in insts:
        last = last_bar_session(s, inst.id)
        if last is not None:
            basis = s.scalar(select(MarketBar.adjustment_basis).where(MarketBar.instrument_id == inst.id).limit(1))
            if basis != p.adjustment_basis:
                mismatches.append(inst.symbol)
                continue  # refuse to mix adjustment conventions
        start = cal.next_session(last) if last else history_start(end, years)
        if start > end:
            continue
        try:
            bars = p.daily_bars(inst.symbol, start, end)
            provider_event(s, f"market:{p.name}", "market", True)
        except (ProviderError, Exception) as e:  # noqa: BLE001 — record and continue other symbols
            errors.append(f"{inst.symbol}: {e}")
            provider_event(s, f"market:{p.name}", "market", False, str(e))
            continue
        rows = [dict(instrument_id=inst.id, session=b.session, open=Decimal(str(b.open)), high=Decimal(str(b.high)),
                     low=Decimal(str(b.low)), close=Decimal(str(b.close)), volume=Decimal(str(b.volume)), provider=p.name,
                     observed_at=b.observed_at, received_at=utcnow(), adjustment_basis=p.adjustment_basis,
                     quality=b.quality, currency="USD") for b in bars if b.close > 0 and b.open > 0]
        if rows:
            for chunk in range(0, len(rows), 1000):
                res = s.execute(pg_insert(MarketBar).values(rows[chunk:chunk + 1000])
                                .on_conflict_do_nothing(index_elements=["instrument_id", "session"]).returning(MarketBar.id))
                inserted += len(res.all())
        if include_actions:
            _ingest_actions(s, p, inst, start, end, errors)
    s.flush()
    status = "ok" if not errors else ("partial" if inserted else "failed")
    return {"status": status, "inserted": inserted, "errors": errors[:20], "provider_mismatch": mismatches,
            "through": end.isoformat()}


def _ingest_actions(s, p, inst, start, end, errors):
    try:
        acts = p.corporate_actions(inst.symbol, start, end)
    except NotSupported:
        acts = []
    except Exception as e:  # noqa: BLE001
        errors.append(f"{inst.symbol} actions: {e}")
        acts = []
    for a in acts:
        s.execute(pg_insert(CorporateAction).values(
            id=__import__("uuid").uuid4().hex, instrument_id=inst.id, type=a.type, ex_date=a.ex_date, pay_date=a.pay_date,
            ratio=Decimal(str(a.ratio)) if a.ratio else None, amount=Decimal(str(a.amount)) if a.amount else None,
            known_at=a.known_at or cal.session_close_utc(cal.prev_session(a.ex_date)), source=p.name,
            needs_manual_reconciliation=a.type in ("merger", "symbol_change"), payload=a.payload, created_at=utcnow(),
        ).on_conflict_do_nothing(index_elements=["instrument_id", "type", "ex_date"]))
        if a.type == "delisting":
            inst.status = "delisted"
            inst.active_to = date.fromisoformat(a.payload.get("last_session")) if a.payload.get("last_session") else a.ex_date
    try:
        evs = p.earnings_events(inst.symbol)
    except NotSupported:
        evs = []
    except Exception as e:  # noqa: BLE001
        errors.append(f"{inst.symbol} events: {e}")
        evs = []
    for ev in evs:
        if ev.event_date > end:
            continue
        s.execute(pg_insert(MarketEvent).values(
            id=__import__("uuid").uuid4().hex, instrument_id=inst.id, type=ev.type, event_date=ev.event_date,
            known_at=ev.known_at, source=p.name, payload=ev.payload, created_at=utcnow(),
        ).on_conflict_do_nothing(index_elements=["instrument_id", "type", "event_date"]))


def bars_for(s: Session, instrument_id: str, start: date | None = None, end: date | None = None) -> list[MarketBar]:
    q = select(MarketBar).where(MarketBar.instrument_id == instrument_id)
    if start:
        q = q.where(MarketBar.session >= start)
    if end:
        q = q.where(MarketBar.session <= end)
    return list(s.scalars(q.order_by(MarketBar.session)).all())


def to_bar(b: MarketBar) -> Bar:
    return Bar(b.session, float(b.open), float(b.high), float(b.low), float(b.close), float(b.volume))


def events_for(s: Session, instrument_id: str) -> list[EarningsEvent]:
    rows = s.scalars(select(MarketEvent).where(MarketEvent.instrument_id == instrument_id, MarketEvent.type == "earnings")
                     .order_by(MarketEvent.event_date)).all()
    return [EarningsEvent(r.event_date, r.known_at, r.payload.get("surprise_pct")) for r in rows]


def load_dataset(s: Session, end: date | None = None, years: int = 6) -> Dataset:
    p = get_market_provider()
    ns = namespace()
    insts = s.scalars(select(Instrument).where(Instrument.namespace == ns)).all()
    start = (end or date.today()) - timedelta(days=int(365.25 * years) + 30)
    data: dict[str, InstrumentData] = {}
    bench = None
    basis = p.adjustment_basis
    for inst in insts:
        rows = bars_for(s, inst.id, start, end)
        if not rows:
            continue
        basis = rows[0].adjustment_basis
        acts = s.scalars(select(CorporateAction).where(CorporateAction.instrument_id == inst.id)).all()
        d = InstrumentData(inst.symbol, inst.type, inst.sector, [to_bar(r) for r in rows], events_for(s, inst.id),
                           {a.ex_date: float(a.ratio) for a in acts if a.type == "split" and a.ratio},
                           {a.ex_date: float(a.amount) for a in acts if a.type == "dividend" and a.amount})
        if inst.symbol == p.benchmark():
            bench = d
        data[inst.symbol] = d
    return Dataset(data, bench, p.name, basis, p.synthetic,
                   event_point_in_time=bool(p.capabilities.get("event_point_in_time", True)), survivorship_free=False)


def data_health(s: Session) -> dict:
    ns = namespace()
    cfg = get_settings()
    latest = cal.latest_finalized_session(datetime.now(timezone.utc), cfg.daily_data_ready_hour_et, cfg.daily_data_ready_minute_et)
    insts = s.scalars(select(Instrument).where(Instrument.namespace == ns)).all()
    stale, missing, last_dates = [], [], []
    for i in insts:
        last = last_bar_session(s, i.id)
        if last is None:
            missing.append(i.symbol)
            continue
        last_dates.append(last)
        if last < latest and i.status == "active":
            age = len(cal.sessions_between(cal.next_session(last), latest))
            if age > 1:
                stale.append({"symbol": i.symbol, "last_session": last.isoformat(), "sessions_behind": age})
    return {"expected_latest_session": latest.isoformat(), "instruments": len(insts),
            "latest_stored_session": max(last_dates).isoformat() if last_dates else None,
            "stale": stale, "missing": missing, "namespace": ns}
