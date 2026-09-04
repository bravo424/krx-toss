from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from krx_toss.agents.handoff import enqueue_qa, enqueue_research, is_pending, path_is_forbidden, read_json
from krx_toss.agents.pnl_brief import build_pnl_brief, brief_markdown, should_run_pnl_today, write_pnl_brief
from krx_toss.config import load_settings
from krx_toss.execution.blotter import Blotter

KST = ZoneInfo("Asia/Seoul")


def test_path_is_forbidden() -> None:
    assert path_is_forbidden("config/creds.csv")
    assert path_is_forbidden(Path("config/nasang_bot_token"))
    assert not path_is_forbidden("config/strategy.yaml")


def test_pnl_brief_and_research_queue(tmp_path: Path) -> None:
    root = tmp_path
    (root / "config").mkdir()
    (root / "config" / "settings.yaml").write_text(
        "\n".join(
            [
                "base_url: https://example.invalid",
                "dry_run: true",
                "timezone: Asia/Seoul",
                "paths:",
                "  cache_dir: data/cache",
                "  blotter_db: data/blotter.sqlite",
                "  kill_switch: data/kill_switch.json",
                "  logs_dir: logs",
                "  signals_path: data/cache/signals.json",
            ]
        ),
        encoding="utf-8",
    )
    (root / "config" / "strategy.yaml").write_text("signal: {}\nentry: {}\nexit: {}\nrisk: {}\n", encoding="utf-8")
    settings = load_settings(root)
    blotter = Blotter(settings.blotter_db)
    blotter.add_realized(date(2026, 9, 4), Decimal("-120000"))
    blotter.close()

    now = datetime(2026, 9, 4, 15, 45, tzinfo=KST)
    assert should_run_pnl_today(settings, session_date=now.date())
    brief = build_pnl_brief(settings, marks={}, now=now)
    assert brief["session_date"] == "2026-09-04"
    assert brief["realized_today_krw"] == "-120000"
    path = write_pnl_brief(settings, brief)
    assert path.exists()
    assert (settings.root / "data" / "agents" / "pnl_brief.md").exists()
    assert "Realized today" in brief_markdown(brief)
    assert is_pending(settings.root / "data" / "agents" / "queue" / "research_request.json")
    assert not should_run_pnl_today(settings, session_date=now.date())


def test_enqueue_qa(tmp_path: Path) -> None:
    path = enqueue_qa(tmp_path, summary="candidate", files=["config/strategy.yaml"], candidate_id="alpha-1")
    data = read_json(path)
    assert data is not None
    assert data["status"] == "pending"
    assert data["candidate_id"] == "alpha-1"
    research = enqueue_research(tmp_path, reason="test", brief_path="data/agents/pnl_brief.json")
    assert is_pending(research)
