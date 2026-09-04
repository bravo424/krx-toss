from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class AgentRunResult:
    role: str
    status: str
    detail: str
    backend: str

    @property
    def ok(self) -> bool:
        return self.status in {"finished", "written", "skipped_ok"}


def _cursor_api_key() -> str | None:
    key = (os.environ.get("CURSOR_API_KEY") or "").strip()
    return key or None


def run_via_sdk(prompt: str, *, cwd: Path, model: str = "composer-2.5") -> AgentRunResult:
    api_key = _cursor_api_key()
    if not api_key:
        return AgentRunResult("?", "error", "CURSOR_API_KEY not set", "sdk")
    try:
        from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions
    except ImportError:
        return AgentRunResult("?", "error", "cursor-sdk not installed (pip install -e \".[agents]\")", "sdk")

    try:
        result = Agent.prompt(
            prompt,
            AgentOptions(
                api_key=api_key,
                model=model,
                local=LocalAgentOptions(cwd=str(cwd)),
            ),
        )
    except CursorAgentError as exc:
        return AgentRunResult("?", "error", f"startup failed: {exc}", "sdk")
    except Exception as exc:  # noqa: BLE001
        return AgentRunResult("?", "error", f"sdk exception: {exc}", "sdk")

    status = getattr(result, "status", None) or "unknown"
    text = getattr(result, "result", None) or getattr(result, "text", None) or ""
    if status == "error":
        return AgentRunResult("?", "error", f"run failed: {text}", "sdk")
    return AgentRunResult("?", "finished", str(text)[:2000], "sdk")


def run_via_agent_cli(prompt: str, *, cwd: Path) -> AgentRunResult:
    exe = shutil.which("agent") or shutil.which("cursor")
    if not exe:
        return AgentRunResult("?", "error", "neither `agent` nor `cursor` CLI found on PATH", "cli")
    # Prefer `agent` chat-style non-interactive if available; otherwise write prompt only.
    cmd = [exe, "-p", prompt] if Path(exe).name.lower().startswith("agent") else None
    if cmd is None:
        return AgentRunResult("?", "error", "cursor CLI present but no non-interactive agent invoke; use SDK", "cli")
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=60 * 45,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return AgentRunResult("?", "error", str(exc), "cli")
    if completed.returncode != 0:
        return AgentRunResult("?", "error", completed.stderr[-1500:] or completed.stdout[-1500:], "cli")
    return AgentRunResult("?", "finished", (completed.stdout or "")[-2000:], "cli")


def write_prompt_file(path: Path, prompt: str) -> AgentRunResult:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prompt + "\n", encoding="utf-8")
    return AgentRunResult("?", "written", str(path), "file")


def invoke_agent(
    role: str,
    prompt: str,
    *,
    cwd: Path,
    prefer: str = "auto",
    model: str = "composer-2.5",
) -> AgentRunResult:
    """Invoke a Cursor agent.

    prefer: auto | sdk | cli | file
    auto tries SDK → CLI → write prompt file for manual/IDE run.
    """
    backends = []
    if prefer == "auto":
        backends = ["sdk", "cli", "file"]
    else:
        backends = [prefer]

    last = AgentRunResult(role, "error", "no backend", "none")
    for backend in backends:
        if backend == "sdk":
            last = run_via_sdk(prompt, cwd=cwd, model=model)
        elif backend == "cli":
            last = run_via_agent_cli(prompt, cwd=cwd)
        elif backend == "file":
            out = cwd / "data" / "agents" / "prompts" / f"{role}.txt"
            last = write_prompt_file(out, prompt)
        else:
            last = AgentRunResult(role, "error", f"unknown backend {backend}", backend)
        last = AgentRunResult(role, last.status, last.detail, last.backend)
        if last.ok or last.status == "written":
            log.info("agent role=%s backend=%s status=%s", role, last.backend, last.status)
            return last
        log.warning("agent role=%s backend=%s failed: %s", role, last.backend, last.detail)
    return last


def alpha_alert_text(candidate: dict[str, Any]) -> str:
    cid = candidate.get("id") or "alpha"
    hyp = candidate.get("hypothesis") or ""
    base = candidate.get("baseline") or {}
    cand = candidate.get("candidate") or {}
    return (
        f"🧠 <b>krx-toss alpha candidate</b>\n"
        f"id=<code>{cid}</code>\n"
        f"{hyp}\n"
        f"baseline return={base.get('total_return')} win={base.get('win_rate')} trades={base.get('trades')}\n"
        f"candidate return={cand.get('total_return')} win={cand.get('win_rate')} trades={cand.get('trades')}\n"
        f"Queued for QA."
    )
