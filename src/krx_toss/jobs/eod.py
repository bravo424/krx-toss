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

    stops = [pos for pos in broker.blotter.positions() if int(pos.get("sessions_held") or 0) >= time_stop]
    marks = broker.last_prices([str(pos["symbol"]) for pos in stops])
    for pos in stops:
        symbol = pos["symbol"]
        market = pos.get("market") or "KOSPI"
        last = marks.get(symbol) or to_decimal(pos["avg_price"])
        broker.flatten(symbol, market, last, int(pos["quantity"]), "time_stop")
    log.info("eod complete positions=%s", len(broker.blotter.positions()))
