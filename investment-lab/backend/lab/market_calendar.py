"""NYSE trading calendar computed from published holiday rules.

Covers: New Year's Day, MLK Day, Presidents' Day, Good Friday, Memorial Day, Juneteenth (2022+),
Independence Day, Labor Day, Thanksgiving, Christmas, with Saturday->Friday / Sunday->Monday
observance (New Year's on Saturday is NOT observed on the prior Friday, per NYSE rule), plus early
closes (13:00 ET) on July 3, the day after Thanksgiving and Christmas Eve when they are sessions.
Special one-off closures (e.g. national days of mourning) are listed in EXTRA_CLOSURES; review it
annually against nyse.com/markets/hours-calendars.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")
OPEN_TIME = time(9, 30)
CLOSE_TIME = time(16, 0)
EARLY_CLOSE_TIME = time(13, 0)

EXTRA_CLOSURES = {
    date(2018, 12, 5),  # President G.H.W. Bush mourning
    date(2025, 1, 9),  # President Carter mourning
}


def _easter(y: int) -> date:
    a = y % 19
    b, c = divmod(y, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(y, month, day)


def _nth_weekday(y: int, month: int, weekday: int, n: int) -> date:
    d = date(y, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(y: int, month: int, weekday: int) -> date:
    d = date(y, month + 1, 1) - timedelta(days=1) if month < 12 else date(y, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date, allow_friday: bool = True) -> date | None:
    if d.weekday() == 5:
        return d - timedelta(days=1) if allow_friday else None
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=64)
def holidays(y: int) -> frozenset[date]:
    hs: set[date | None] = {
        _observed(date(y, 1, 1), allow_friday=False),
        _nth_weekday(y, 1, 0, 3),
        _nth_weekday(y, 2, 0, 3),
        _easter(y) - timedelta(days=2),
        _last_weekday(y, 5, 0),
        _observed(date(y, 7, 4)),
        _nth_weekday(y, 9, 0, 1),
        _nth_weekday(y, 11, 3, 4),
        _observed(date(y, 12, 25)),
    }
    if y >= 2022:
        hs.add(_observed(date(y, 6, 19)))
    # New Year's of next year falling on Saturday is not observed on Dec 31 (NYSE rule).
    hs |= {d for d in EXTRA_CLOSURES if d.year == y}
    return frozenset(d for d in hs if d is not None)


@lru_cache(maxsize=64)
def early_closes(y: int) -> frozenset[date]:
    out = set()
    for d in (date(y, 7, 3), _nth_weekday(y, 11, 3, 4) + timedelta(days=1), date(y, 12, 24)):
        if is_session(d):
            out.add(d)
    return frozenset(out)


def is_session(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def next_session(d: date) -> date:
    d = d + timedelta(days=1)
    while not is_session(d):
        d += timedelta(days=1)
    return d


def prev_session(d: date) -> date:
    d = d - timedelta(days=1)
    while not is_session(d):
        d -= timedelta(days=1)
    return d


def sessions_between(start: date, end: date) -> list[date]:
    """Inclusive list of sessions in [start, end]."""
    out = []
    d = start
    while d <= end:
        if is_session(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def add_sessions(d: date, n: int) -> date:
    for _ in range(n):
        d = next_session(d)
    return d


def session_open_utc(d: date) -> datetime:
    return datetime.combine(d, OPEN_TIME, ET).astimezone(timezone.utc)


def session_close_utc(d: date) -> datetime:
    t = EARLY_CLOSE_TIME if d in early_closes(d.year) else CLOSE_TIME
    return datetime.combine(d, t, ET).astimezone(timezone.utc)


def data_ready_utc(d: date, hour: int = 18, minute: int = 30) -> datetime:
    """Time after which the provider's finalized EOD bar for session d is assumed available."""
    return datetime.combine(d, time(hour, minute), ET).astimezone(timezone.utc)


def latest_finalized_session(now_utc: datetime, hour: int = 18, minute: int = 30) -> date:
    d = now_utc.astimezone(ET).date()
    if is_session(d) and now_utc >= data_ready_utc(d, hour, minute):
        return d
    return prev_session(d)


def first_session_with_close_after(t_utc: datetime) -> date:
    """First session whose close is strictly after t_utc (signals for an approval made at t_utc)."""
    d = t_utc.astimezone(ET).date()
    if not is_session(d):
        d = next_session(d)
    while session_close_utc(d) <= t_utc:
        d = next_session(d)
    return d


def first_open_after(t_utc: datetime) -> date:
    """First session whose regular open is strictly after t_utc (never fill at an earlier open)."""
    d = t_utc.astimezone(ET).date()
    if not is_session(d):
        d = next_session(d)
    while session_open_utc(d) <= t_utc:
        d = next_session(d)
    return d
