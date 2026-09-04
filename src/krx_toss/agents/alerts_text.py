from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from krx_toss.agents.runner import AgentRunResult

KST = ZoneInfo("Asia/Seoul")


def _esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def _now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")


def agent_commenced(role: str, *, backend: str | None = None) -> str:
    backend_bit = f"\nbackend=<code>{_esc(backend)}</code>" if backend else ""
    return (
        f"▶️ <b>krx-toss agent started</b>\n"
        f"🕐 {_now_kst()}\n"
        f"role=<code>{_esc(role)}</code>{backend_bit}"
    )


def agent_completed(
    role: str,
    result: AgentRunResult,
    *,
    git: dict[str, str] | None = None,
) -> str:
    ok = result.ok
    emoji = "✅" if ok else "❌"
    lines = [
        f"{emoji} <b>krx-toss agent finished</b>",
        f"🕐 {_now_kst()}",
        f"role=<code>{_esc(role)}</code>",
        f"status=<code>{_esc(result.status)}</code>  backend=<code>{_esc(result.backend)}</code>",
    ]
    if result.detail:
        detail = result.detail if len(result.detail) <= 400 else result.detail[:397] + "…"
        lines.append(f"<code>{_esc(detail)}</code>")
    if git:
        gstatus = git.get("status") or "unknown"
        gdetail = git.get("detail") or ""
        if gstatus == "pushed":
            lines.append(f"📤 GitHub: pushed <code>{_esc(git.get('commit', '?'))}</code> ({_esc(gdetail)})")
        elif gstatus == "skipped":
            lines.append(f"📤 GitHub: no publishable changes")
        else:
            lines.append(f"📤 GitHub: {_esc(gstatus)} — {_esc(gdetail)}")
    return "\n".join(lines)


def tick_summary(outcomes: dict[str, str]) -> str:
    if not outcomes or outcomes == {"idle": "ok"}:
        return ""
    parts = [f"{role}={status}" for role, status in outcomes.items()]
    return f"📋 <b>krx-toss agents tick</b>\n🕐 {_now_kst()}\n" + "\n".join(f"• <code>{_esc(p)}</code>" for p in parts)
