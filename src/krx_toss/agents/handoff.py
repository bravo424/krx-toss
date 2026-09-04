from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

FORBIDDEN_PATH_FRAGMENTS = (
    "creds.csv",
    "nasang_bot_token",
    "position_bot_token",
    "kill_switch.json",
)


def agents_root(root: Path) -> Path:
    path = root / "data" / "agents"
    path.mkdir(parents=True, exist_ok=True)
    (path / "queue").mkdir(parents=True, exist_ok=True)
    return path


def _now_kst() -> datetime:
    return datetime.now(KST)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def append_run_log(root: Path, event: dict[str, Any]) -> None:
    log_path = agents_root(root) / "runs.jsonl"
    row = {"ts": _now_kst().isoformat(), **event}
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def queue_path(root: Path, name: str) -> Path:
    return agents_root(root) / "queue" / name


def is_pending(path: Path) -> bool:
    data = read_json(path)
    return bool(data and data.get("status") == "pending")


def mark_status(path: Path, status: str, **extra: Any) -> dict[str, Any]:
    data = read_json(path) or {}
    data["status"] = status
    data["updated_at"] = _now_kst().isoformat()
    data.update(extra)
    write_json(path, data)
    return data


def enqueue_research(root: Path, *, reason: str, brief_path: str) -> Path:
    path = queue_path(root, "research_request.json")
    write_json(
        path,
        {
            "status": "pending",
            "reason": reason,
            "brief_path": brief_path,
            "created_at": _now_kst().isoformat(),
        },
    )
    return path


def enqueue_qa(
    root: Path,
    *,
    summary: str,
    files: list[str],
    candidate_id: str | None = None,
) -> Path:
    path = queue_path(root, "qa_request.json")
    write_json(
        path,
        {
            "status": "pending",
            "summary": summary,
            "files": files,
            "candidate_id": candidate_id,
            "created_at": _now_kst().isoformat(),
        },
    )
    return path


def enqueue_promote(root: Path, *, status: str, summary: str, risks: list[str] | None = None) -> Path:
    path = queue_path(root, "promote_request.json")
    write_json(
        path,
        {
            "status": status,
            "summary": summary,
            "risks": risks or [],
            "requires_human_ack": True,
            "created_at": _now_kst().isoformat(),
        },
    )
    return path


def path_is_forbidden(path: str | Path) -> bool:
    text = str(path).replace("\\", "/").lower()
    return any(frag in text for frag in FORBIDDEN_PATH_FRAGMENTS)
