from datetime import datetime

from tarka_md.schedule import NPT, in_window, next_window_start


def test_window_trading_day():
    assert in_window(datetime(2026, 10, 4, 10, 30, tzinfo=NPT))  # Sunday pre-open
    assert in_window(datetime(2026, 10, 4, 14, 59, tzinfo=NPT))
    assert not in_window(datetime(2026, 10, 4, 15, 30, tzinfo=NPT))
    assert not in_window(datetime(2026, 10, 2, 12, 0, tzinfo=NPT))  # Friday


def test_next_window_skips_weekend():
    thu_evening = datetime(2026, 10, 8, 16, 0, tzinfo=NPT)
    assert next_window_start(thu_evening) == datetime(2026, 10, 11, 10, 25, tzinfo=NPT)  # Sunday


def test_next_window_same_morning_and_from_utc():
    morning = datetime(2026, 10, 5, 9, 0, tzinfo=NPT)
    assert next_window_start(morning) == datetime(2026, 10, 5, 10, 25, tzinfo=NPT)
    utc = datetime.fromisoformat("2026-10-05T03:15:00+00:00")  # 09:00 NPT
    assert next_window_start(utc) == datetime(2026, 10, 5, 10, 25, tzinfo=NPT)
