from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from krx_toss.agents.handoff import path_is_forbidden

log = logging.getLogger(__name__)

# Never publish local runtime config, secrets, caches, or agent handoff scratch.
_SKIP_PREFIXES = (
    "config/",
    "data/",
    "logs/",
    ".env",
    ".venv/",
)
_SKIP_EXACT = {
    ".env",
}


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _safe_path(path: str) -> bool:
    normalized = path.replace("\\", "/").strip()
    if not normalized or normalized.startswith("../"):
        return False
    if normalized in _SKIP_EXACT:
        return False
    if any(normalized.startswith(prefix) for prefix in _SKIP_PREFIXES):
        return False
    if path_is_forbidden(normalized):
        return False
    return True


def list_publishable_changes(root: Path) -> list[str]:
    completed = _run_git(["status", "--porcelain"], cwd=root)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git status failed")
    paths: list[str] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        # XY path or XY old -> new
        payload = line[3:].strip()
        if " -> " in payload:
            _, new_path = payload.split(" -> ", 1)
            candidate = new_path.strip()
        else:
            candidate = payload.strip()
        if _safe_path(candidate):
            paths.append(candidate)
    return paths


def publish_agent_changes(
    root: Path,
    *,
    role: str,
    summary: str | None = None,
) -> dict[str, str]:
    """Commit and push tracked source changes after an agent run.

    Skips config/, data/, logs/, credentials, and other forbidden paths.
    Returns a small result dict for alerts/logging.
    """
    try:
        paths = list_publishable_changes(root)
    except RuntimeError as exc:
        return {"status": "error", "detail": str(exc)}

    if not paths:
        return {"status": "skipped", "detail": "no publishable changes"}

    add = _run_git(["add", "--", *paths], cwd=root)
    if add.returncode != 0:
        return {"status": "error", "detail": add.stderr.strip() or "git add failed"}

    body = summary or f"Agent {role} updates."
    subject = f"agents: {role}"
    commit = _run_git(["commit", "-m", subject, "-m", body], cwd=root)
    if commit.returncode != 0:
        detail = commit.stderr.strip() or commit.stdout.strip() or "git commit failed"
        if "nothing to commit" in detail.lower():
            return {"status": "skipped", "detail": "nothing to commit after add"}
        return {"status": "error", "detail": detail}

    push = _run_git(["push"], cwd=root)
    if push.returncode != 0:
        return {
            "status": "error",
            "detail": push.stderr.strip() or push.stdout.strip() or "git push failed",
            "committed": subject,
        }

    sha = _run_git(["rev-parse", "--short", "HEAD"], cwd=root)
    commit_sha = sha.stdout.strip() if sha.returncode == 0 else "?"
    log.info("agent git publish role=%s files=%s sha=%s", role, len(paths), commit_sha)
    return {
        "status": "pushed",
        "detail": f"{len(paths)} file(s)",
        "commit": commit_sha,
        "files": ", ".join(paths[:8]) + ("…" if len(paths) > 8 else ""),
    }
