from __future__ import annotations

import subprocess
from pathlib import Path

from krx_toss.agents.git_publish import _safe_path, list_publishable_changes, publish_agent_changes


def _git(args: list[str], *, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def test_safe_path_rules() -> None:
    assert _safe_path("src/krx_toss/agents/runner.py")
    assert not _safe_path("config/strategy.yaml")
    assert not _safe_path("config/creds.csv")
    assert not _safe_path("data/agents/pnl_brief.json")
    assert not _safe_path(".env")


def test_publish_agent_changes(tmp_path: Path, monkeypatch) -> None:
    import krx_toss.agents.git_publish as gp

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init"], cwd=repo)
    _git(["config", "user.email", "test@example.com"], cwd=repo)
    _git(["config", "user.name", "test"], cwd=repo)
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "note.txt").write_text("hello", encoding="utf-8")
    _git(["add", "src/note.txt"], cwd=repo)
    _git(["commit", "-m", "init"], cwd=repo)
    (repo / "src" / "note.txt").write_text("agent edit", encoding="utf-8")

    real_run_git = gp._run_git

    def _run_git(args: list[str], *, cwd: Path):
        if args == ["push"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return real_run_git(args, cwd=cwd)

    monkeypatch.setattr(gp, "_run_git", _run_git)

    paths = list_publishable_changes(repo)
    assert paths == ["src/note.txt"]

    result = publish_agent_changes(repo, role="qa", summary="test")
    assert result["status"] == "pushed"
