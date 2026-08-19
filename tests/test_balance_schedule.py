from __future__ import annotations

from datetime import time

from krx_toss.jobs.telegram_job import next_balance_kind


def _kind(**kwargs):
    defaults = dict(
        open_today=True,
        session_start="09:00",
        session_end="15:30",
        open_sent=False,
        close_sent=False,
        hourly_due=False,
    )
    defaults.update(kwargs)
    return next_balance_kind(**defaults)


def test_no_balance_on_holiday():
    assert _kind(open_today=False, clock=time(10, 0)) is None


def test_before_open_is_silent():
    assert _kind(clock=time(8, 59)) is None


def test_session_open_at_0900():
    assert _kind(clock=time(9, 0)) == "open"


def test_mid_session_sends_open_once_then_hourly():
    assert _kind(clock=time(10, 5), open_sent=False) == "open"
    assert _kind(clock=time(10, 5), open_sent=True, hourly_due=False) is None
    assert _kind(clock=time(11, 5), open_sent=True, hourly_due=True) == "hourly"


def test_hourly_stops_at_1500():
    assert _kind(clock=time(14, 59), open_sent=True, hourly_due=True) == "hourly"
    assert _kind(clock=time(15, 0), open_sent=True, hourly_due=True) is None
    assert _kind(clock=time(15, 20), open_sent=True, hourly_due=True) is None


def test_session_close_at_1530():
    assert _kind(clock=time(15, 30), open_sent=True) == "close"
    assert _kind(clock=time(16, 0), open_sent=True, close_sent=True) is None


def test_late_start_after_close_sends_close_only():
    assert _kind(clock=time(16, 10), open_sent=False, close_sent=False) == "close"
    assert _kind(clock=time(16, 10), open_sent=False, close_sent=True) is None


def test_odd_session_start_does_not_crash():
    assert _kind(clock=time(22, 0), session_start="09", session_end="15:30", open_sent=False) == "close"
    assert _kind(clock=time(10, 0), session_start="2026-08-19T09:00:00+09:00", session_end="15:30") == "open"
