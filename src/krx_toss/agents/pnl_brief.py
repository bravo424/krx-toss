from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from krx_toss.agents.handoff import agents_root, enqueue_research, write_json
from krx_toss.config import Settings
from krx_toss.execution.blotter import Blotter
from krx_toss.execution.kill_switch import KillSwitch
from krx_toss.toss.decimal_utils import to_decimal

KST = ZoneInfo("Asia/Seoul")


def _upnl(positions: list[dict[str, Any]], marks: dict[str, Decimal]) -> Decimal:
    total = Decimal("0")
    for pos in positions:
        qty = int(pos.get("quantity") or 0)
        if qty <= 0:
            continue
        entry = to_decimal(pos.get("avg_price") or 0)
        mark = marks.get(str(pos.get("symbol") or ""), entry)
        total += (mark - entry) * qty
    return total


def build_pnl_brief(
    settings: Settings,
    *,
    marks: dict[str, Decimal] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(KST)
    blotter = Blotter(settings.blotter_db)
    try:
        positions = blotter.positions()
        realized = blotter.realized_on(now.date())
    finally:
        blotter.close()
    marks = marks or {}
    open_upnl = _upnl(positions, marks)
    kill = KillSwitch(settings.kill_switch).status()
    findings: list[str] = []
    if realized < 0:
        findings.append(f"Realized loss today {_sgn(realized)} KRW — review stops and entry quality.")
    elif realized > 0:
        findings.append(f"Realized gain today {_sgn(realized)} KRW — check if winners hit TP early or late.")
    if not positions and realized == 0:
        findings.append("Flat book and zero realized — verify scan acceptance rate and entry gates.")
    if kill.get("tripped"):
        findings.append(f"Kill switch tripped: {kill.get('reason')}")

    hypotheses = [
        "Tighten or loosen max_3d_return / dip-reversal bands based on today's losers.",
        "Test require_both_flows=true vs false on the current watchlist regime.",
        "Sweep stop_loss / take_profit / lock_profit for net PNL after 0.20% sell tax.",
    ]
    sweeps = [
        {"section": "signal", "key": "max_3d_return", "range": [0.13, 0.20]},
        {"section": "signal", "key": "reversal_min_1d", "range": [-0.02, -0.01]},
        {"section": "exit", "key": "stop_loss", "range": [0.03, 0.05]},
    ]
    return {
        "as_of": now.isoformat(),
        "session_date": now.date().isoformat(),
        "dry_run": settings.dry_run,
        "realized_today_krw": str(realized),
        "open_upnl_krw": str(open_upnl),
        "positions": [
            {
                "symbol": p.get("symbol"),
                "quantity": p.get("quantity"),
                "avg_price": str(p.get("avg_price")),
                "sessions_held": p.get("sessions_held"),
                "market": p.get("market"),
            }
            for p in positions
        ],
        "kill_switch": kill,
        "findings": findings,
        "research_hypotheses": hypotheses,
        "suggested_param_sweeps": sweeps,
        "do_not_change": ["dry_run", "creds", "kill_switch", "telegram tokens"],
    }


def _sgn(value: Decimal) -> str:
    return f"+{value:,.0f}" if value >= 0 else f"{value:,.0f}"


def brief_markdown(brief: dict[str, Any]) -> str:
    lines = [
        f"# PNL brief — {brief.get('session_date')}",
        "",
        f"- As of: `{brief.get('as_of')}`",
        f"- Dry run: `{brief.get('dry_run')}`",
        f"- Realized today: **{brief.get('realized_today_krw')} KRW**",
        f"- Open uPNL: **{brief.get('open_upnl_krw')} KRW**",
        f"- Positions: **{len(brief.get('positions') or [])}**",
        "",
        "## Findings",
    ]
    for item in brief.get("findings") or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Research hypotheses"])
    for item in brief.get("research_hypotheses") or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Suggested param sweeps"])
    for sweep in brief.get("suggested_param_sweeps") or []:
        lines.append(f"- `{sweep.get('section')}.{sweep.get('key')}` → {sweep.get('range')}")
    lines.append("")
    return "\n".join(lines)


def write_pnl_brief(settings: Settings, brief: dict[str, Any]) -> Path:
    root = agents_root(settings.root)
    json_path = root / "pnl_brief.json"
    md_path = root / "pnl_brief.md"
    write_json(json_path, brief)
    md_path.write_text(brief_markdown(brief), encoding="utf-8")
    enqueue_research(
        settings.root,
        reason="post_close_pnl_brief",
        brief_path=str(json_path.relative_to(settings.root)).replace("\\", "/"),
    )
    return json_path


def should_run_pnl_today(settings: Settings, *, session_date: date | None = None) -> bool:
    session_date = session_date or datetime.now(KST).date()
    existing = None
    path = agents_root(settings.root) / "pnl_brief.json"
    if path.exists():
        import json

        existing = json.loads(path.read_text(encoding="utf-8"))
    return not (isinstance(existing, dict) and existing.get("session_date") == session_date.isoformat())
