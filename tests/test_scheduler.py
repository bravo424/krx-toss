from __future__ import annotations

from krx_toss.jobs.scheduler import loop_stall_seconds
from krx_toss.keep_awake import KeepAwake


def test_loop_stall_ignores_first_tick():
    assert loop_stall_seconds(0.0, 10_000.0) is None


def test_loop_stall_detects_sleep_gap():
    assert loop_stall_seconds(100.0, 190.0) == 90.0
    assert loop_stall_seconds(100.0, 189.0) is None


def test_keep_awake_is_noop_off_windows(monkeypatch):
    monkeypatch.setattr("krx_toss.keep_awake.sys.platform", "linux")
    with KeepAwake() as awake:
        awake.ping()
    assert awake._armed is False
