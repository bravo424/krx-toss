from __future__ import annotations

import logging
from datetime import date

from krx_toss.config import Settings
from krx_toss.execution.broker import Broker
from krx_toss.jobs.open_entry import estimate_nav
from krx_toss.strategy.risk import RiskLimits, daily_loss_breached
from krx_toss.toss.client import TossClient
from krx_toss.toss.decimal_utils import to_decimal

log = logging.getLogger(__name__)


def run_eod(client: TossClient, broker: Broker, settings: Settings) -> None:
    exit_cfg = settings.strategy_section("exit")
    time_stop = int(exit_cfg.get("time_stop_sessions", 5))
    broker.blotter.bump_sessions()
    limits = RiskLimits.from_strategy(settings.strategy)
    nav = estimate_nav(broker)
    realized = broker.blotter.realized_on(date.today())
    if daily_loss_breached(nav, realized, limits):
        broker.kill_switch.trip(f"daily_loss {realized}")
        log.error("daily loss kill switch tripped: %s", realized)
        broker.alerts.kill_switch(f"daily_loss {realized}")

    for pos in list(broker.blotter.positions()):
        sessions = int(pos.get("sessions_held") or 0)
        if sessions < time_stop:
            continue
        symbol = pos["symbol"]
        market = pos.get("market") or "KOSPI"
        try:
            prices = client.get_prices([symbol])
            last = to_decimal((prices[0] if prices else {}).get("lastPrice") or pos["avg_price"])
        except Exception:  # noqa: BLE001
            last = to_decimal(pos["avg_price"])
        broker.flatten(symbol, market, last, int(pos["quantity"]), "time_stop")
    log.info("eod complete positions=%s", len(broker.blotter.positions()))
