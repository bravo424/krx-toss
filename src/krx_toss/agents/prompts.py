from __future__ import annotations

from pathlib import Path


def skill_invocation(role: str) -> str:
    return f"Follow the project skill `{role}` in `.cursor/skills/{role}/SKILL.md`."


def pnl_prompt(*, root: Path, brief_path: Path) -> str:
    return "\n".join(
        [
            skill_invocation("pnl-analysis"),
            f"Project root: {root}",
            f"A deterministic brief was pre-written at `{brief_path.as_posix()}`.",
            "Enrich findings/hypotheses using blotter, signals.json, strategy.yaml, and docs/TRADING.md.",
            "Update data/agents/pnl_brief.json and pnl_brief.md; keep research_request pending.",
            "Do not place orders or change dry_run / credentials / kill switch.",
        ]
    )


def research_prompt(*, root: Path) -> str:
    return "\n".join(
        [
            skill_invocation("strategy-researcher"),
            f"Project root: {root}",
            "Read data/agents/pnl_brief.json and data/agents/queue/research_request.json.",
            "Search for cost-aware alpha. Prefer config/strategy.yaml changes.",
            "If you find a candidate: update files, write data/agents/alpha_candidate.json,",
            "enqueue data/agents/queue/qa_request.json (status pending), mark research_request done.",
            "The orchestrator will send the Telegram alpha alert — include metrics in alpha_candidate.json.",
            "Never enable live trading.",
        ]
    )


def qa_prompt(*, root: Path) -> str:
    return "\n".join(
        [
            skill_invocation("qa-agent"),
            f"Project root: {root}",
            "Read data/agents/queue/qa_request.json and any alpha_candidate.json.",
            "Review diff, run pytest, run krx-toss backtest when cache exists.",
            "Fix bugs in-scope only. Write data/agents/queue/promote_request.json (approved|rejected).",
            "Mark qa_request done. Never flip dry_run or reset kill switch.",
        ]
    )


def trading_prompt(*, root: Path) -> str:
    return "\n".join(
        [
            skill_invocation("trading-agent"),
            f"Project root: {root}",
            "Oversee handoffs under data/agents/, trading health (status/kill switch), and agent queue.",
            "Do not auto-promote to live. Require human ack for promote_request approved.",
        ]
    )
