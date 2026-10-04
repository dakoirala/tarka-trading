"""NEPSE trading calendar gate (cheap clock check before asking the exchange).

NEPSE trades Sunday-Thursday, pre-open 10:30-10:45 and continuous 11:00-15:00 Nepal time
(UTC+05:45). Holidays are not modelled here: inside the window the collector still asks
the market-open endpoint, which is the authority.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

NPT = timezone(timedelta(hours=5, minutes=45), "NPT")

TRADING_WEEKDAYS = {6, 0, 1, 2, 3}  # Sun..Thu (datetime.weekday: Mon=0)
# Padded on both sides so pre-open and the closing book are captured.
WINDOW_START = time(10, 25)
WINDOW_END = time(15, 10)


def now_npt() -> datetime:
    return datetime.now(NPT)


def trade_date(ts: datetime) -> date:
    return ts.astimezone(NPT).date()


def in_window(ts: datetime) -> bool:
    local = ts.astimezone(NPT)
    return local.weekday() in TRADING_WEEKDAYS and WINDOW_START <= local.time() < WINDOW_END


def next_window_start(ts: datetime) -> datetime:
    """The next time the capture window opens strictly after ``ts`` if not already inside it."""
    local = ts.astimezone(NPT)
    if in_window(local):
        return local
    day = local.date()
    for offset in range(8):
        d = day + timedelta(days=offset)
        start = datetime.combine(d, WINDOW_START, NPT)
        if d.weekday() in TRADING_WEEKDAYS and start > local:
            return start
    raise AssertionError("unreachable: a trading weekday occurs within a week")


def ns_to_npt(ns: int) -> datetime:
    return datetime.fromtimestamp(ns / 1e9, NPT)
