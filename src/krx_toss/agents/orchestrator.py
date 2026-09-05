from __future__ import annotations

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from krx_toss.agents.handoff import (
    agents_root,
    append_run_log,
    is_pending,
    queue_path,
    read_json,
)
from krx_toss.agents.alerts_text import agent_commenced, agent_completed, tick_summary
from krx_toss.agents.git_publish import publish_agent_changes
from krx_toss.agents.pnl_brief import build_pnl_brief, should_run_pnl_today, write_pnl_brief
from krx_toss.agents.prompts import pnl_prompt, qa_prompt, research_prompt, trading_prompt
from krx_toss.agents.runner import alpha_alert_text, invoke_agent
from krx_toss.alerts import TradingAlerts
from krx_toss.config import Settings
from krx_toss.execution.broker import Broker
from krx_toss.jobs.calendar import calendar_is_open
from krx_toss.toss.client import TossClient

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")

# Post-close PNL window: after 15:30 KST (regular session end).
PNL_AFTER_HOUR = 15
PNL_AFTER_MINUTE = 30
RESEARCH_INTERVAL_SECONDS = 6 * 3600  # documented cadence; queue-driven, not a busy loop
DEFAULT_SLEEP_SECONDS = 60


def _open_today(client: TossClient | None, now: datetime) -> bool:
    if client is None:
        return now.weekday() < 5
    try:
        cal = client.get_kr_calendar()
        return calendar_is_open(cal, now)
    except Exception as exc:  # noqa: BLE001
        log.warning("calendar fetch failed for agents: %s", exc)
        return now.weekday() < 5


def _send_trade_alert(alerts: TradingAlerts | None, text: str) -> None:
    if alerts is None or alerts.trade is None:
        return
    try:
        alerts.trade.send(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("agent telegram failed: %s", exc)


def run_agents_once(
    settings: Settings,
    *,
    client: TossClient | None = None,
    broker: Broker | None = None,
    alerts: TradingAlerts | None = None,
    prefer: str = "auto",
    force_role: str | None = None,
    invoke_llm: bool = True,
) -> dict[str, str]:
    """One supervisor pass. Returns map of role -> status."""
    now = datetime.now(KST)
    alerts = alerts or (broker.alerts if broker is not None else None)
    results: dict[str, str] = {}
    root = settings.root
    agents_root(root)

    open_today = _open_today(client, now)
    clock_ok_for_pnl = (now.hour > PNL_AFTER_HOUR) or (
        now.hour == PNL_AFTER_HOUR and now.minute >= PNL_AFTER_MINUTE
    )

    def _run(role: str, prompt: str) -> str:
        if not invoke_llm:
            out = root / "data" / "agents" / "prompts" / f"{role}.txt"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(prompt + "\n", encoding="utf-8")
            append_run_log(root, {"role": role, "status": "written", "backend": "file"})
            return "written"

        backend_hint = "cli" if prefer in ("auto", "cli") else prefer
        _send_trade_alert(alerts, agent_commenced(role, backend=backend_hint))
        result = invoke_agent(role, prompt, cwd=root, prefer=prefer)
        append_run_log(
            root,
            {
                "role": role,
                "status": result.status,
                "backend": result.backend,
                "detail": result.detail[:500],
            },
        )
        git_result: dict[str, str] | None = None
        if result.ok:
            git_result = publish_agent_changes(root, role=role, summary=result.detail[:500])
        _send_trade_alert(alerts, agent_completed(role, result, git=git_result))
        return result.status

    # --- PNL analysis ---
    need_pnl = force_role == "pnl" or (
        force_role is None and open_today and clock_ok_for_pnl and should_run_pnl_today(settings)
    )
    if need_pnl or force_role == "pnl":
        marks = {}
        if broker is not None:
            try:
                symbols = [str(p["symbol"]) for p in broker.blotter.positions()]
                marks = broker.last_prices(symbols) if symbols else {}
            except Exception as exc:  # noqa: BLE001
                log.warning("mark fetch failed: %s", exc)
        brief = build_pnl_brief(settings, marks=marks, now=now)
        brief_path = write_pnl_brief(settings, brief)
        _send_trade_alert(
            alerts,
            (
                f"📉 <b>krx-toss PNL brief</b>\n"
                f"{brief['session_date']} realized={brief['realized_today_krw']} "
                f"uPNL={brief['open_upnl_krw']}\n"
                f"Research queued."
            ),
        )
        if invoke_llm or force_role == "pnl":
            results["pnl-analysis"] = _run("pnl-analysis", pnl_prompt(root=root, brief_path=brief_path))
        else:
            results["pnl-analysis"] = "brief_written"

    # --- Strategy researcher ---
    research_q = queue_path(root, "research_request.json")
    need_research = force_role == "research" or (force_role is None and is_pending(research_q))
    if need_research:
        results["strategy-researcher"] = _run("strategy-researcher", research_prompt(root=root))
        candidate = read_json(agents_root(root) / "alpha_candidate.json")
        qa_q = queue_path(root, "qa_request.json")
        if candidate and is_pending(qa_q):
            _send_trade_alert(alerts, alpha_alert_text(candidate))

    # --- QA ---
    qa_q = queue_path(root, "qa_request.json")
    need_qa = force_role == "qa" or (force_role is None and is_pending(qa_q))
    if need_qa:
        results["qa"] = _run("qa", qa_prompt(root=root))

    # --- Trading supervisor glance ---
    if force_role in (None, "trading"):
        promote = read_json(queue_path(root, "promote_request.json"))
        promote_needs_ack = bool(
            promote
            and promote.get("status") == "approved"
            and promote.get("requires_human_ack", True)
        )
        if promote_needs_ack:
            _send_trade_alert(
                alerts,
                (
                    "✅ <b>krx-toss QA approved</b>\n"
                    f"{promote.get('summary')}\n"
                    "Human ack required before paper/live apply. dry_run will NOT be flipped automatically."
                ),
            )
        # Skip empty LLM ticks (weekend / idle): only when forced, promote needs
        # human ack, or PNL/research/QA already ran this pass.
        need_trading = force_role == "trading" or promote_needs_ack or bool(results)
        if need_trading and (force_role == "trading" or invoke_llm):
            results["trading-agent"] = _run("trading-agent", trading_prompt(root=root))

    if not results:
        results["idle"] = "ok"
    else:
        summary = tick_summary(results)
        if summary:
            _send_trade_alert(alerts, summary)
    return results


def run_agents(
    settings: Settings,
    *,
    client: TossClient | None = None,
    broker: Broker | None = None,
    once: bool = False,
    prefer: str = "auto",
    invoke_llm: bool = True,
    sleep_seconds: int = DEFAULT_SLEEP_SECONDS,
) -> None:
    """Trading-agent supervisor loop."""
    alerts = broker.alerts if broker is not None else None
    _send_trade_alert(alerts, "🟢 <b>krx-toss agents</b> supervisor started")
    while True:
        try:
            outcomes = run_agents_once(
                settings,
                client=client,
                broker=broker,
                alerts=alerts,
                prefer=prefer,
                invoke_llm=invoke_llm,
            )
            log.info("agents tick: %s", outcomes)
        except Exception as exc:  # noqa: BLE001
            log.exception("agents tick failed: %s", exc)
            _send_trade_alert(alerts, f"🔴 <b>krx-toss agents</b> tick failed\n<code>{exc}</code>")
        if once:
            _send_trade_alert(alerts, f"⏹️ <b>krx-toss agents</b> supervisor finished\n🕐 one pass complete")
            return
        time.sleep(max(5, sleep_seconds))
