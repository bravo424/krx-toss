from __future__ import annotations

from pathlib import Path

import pytest

from krx_toss.cli import main
from krx_toss.config import load_creds, load_settings


def test_creds_loader(tmp_path: Path):
    path = tmp_path / "creds.csv"
    path.write_text("client_id,client_secret\nabc,def\n", encoding="utf-8")
    assert load_creds(path) == ("abc", "def")


def test_creds_placeholder_rejected(tmp_path: Path):
    path = tmp_path / "creds.csv"
    path.write_text("client_id,client_secret\nYOUR_CLIENT_ID,YOUR_CLIENT_SECRET\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_creds(path)


def test_settings_load_from_project_root():
    settings = load_settings()
    assert settings.base_url.endswith("tossinvest.com")
    assert settings.signals_path.name == "signals.json"


def test_live_requires_confirmation():
    assert main(["live"]) == 2
