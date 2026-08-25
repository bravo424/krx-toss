from __future__ import annotations

from datetime import date
from pathlib import Path

from krx_toss.execution.kill_switch import KillSwitch


def test_same_day_trip_blocks(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("krx_toss.execution.kill_switch.datetime", _FrozenDate(date(2026, 8, 25)))
    kill = KillSwitch(tmp_path / "kill.json")
    kill.trip("daily_loss")
    assert kill.tripped()
    assert kill.status()["on"] == "2026-08-25"


def test_next_day_auto_resets(tmp_path: Path, monkeypatch):
    clock = _FrozenDate(date(2026, 8, 19))
    monkeypatch.setattr("krx_toss.execution.kill_switch.datetime", clock)
    kill = KillSwitch(tmp_path / "kill.json")
    kill.trip("daily_loss")
    clock.day = date(2026, 8, 25)
    assert not kill.tripped()
    assert not (tmp_path / "kill.json").exists()


def test_legacy_file_without_on_resets(tmp_path: Path):
    path = tmp_path / "kill.json"
    path.write_text('{"tripped": true, "reason": "daily_loss"}', encoding="utf-8")
    kill = KillSwitch(path)
    assert not kill.tripped()
    assert not path.exists()


class _FrozenDate:
    def __init__(self, day: date) -> None:
        self.day = day

    def now(self, tz=None):  # noqa: ANN001
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz = tz or ZoneInfo("Asia/Seoul")
        return datetime(self.day.year, self.day.month, self.day.day, 10, 0, tzinfo=tz)
