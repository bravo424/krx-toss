from __future__ import annotations

import logging
from decimal import Decimal

from krx_toss.config import Settings
from krx_toss.execution.broker import Broker
from krx_toss.execution.overlay import last_from_candles, overlay_actions
from krx_toss.toss.client import TossClient
from krx_toss.toss.decimal_utils import to_decimal

log = logging.getLogger(__name__)


def run_overlay(client: TossClient, broker: Broker, settings: Settings) -> list[str]:
    exit_cfg = settings.strategy_section("exit")
    uni = settings.strategy_section("universe")
    near = to_decimal(exit_cfg.get("flatten_near_limit_pct", "0.02"))
    flatten_vi = bool(exit_cfg.get("overlay_vi_flatten", True))
    blocked = {str(x).upper() for x in (uni.get("blocked_warning_types") or [])}
    actions: list[str] = []
    for pos in broker.blotter.positions():
        symbol = pos["symbol"]
        market = pos.get("market") or "KOSPI"
        try:
            minute = client.get_candles(symbol, interval="1m", count=5)
            warnings = client.get_warnings(symbol)
            limits = client.get_price_limits(symbol)
        except Exception as exc:  # noqa: BLE001
            log.warning("overlay fetch failed %s: %s", symbol, exc)
            continue
        last = last_from_candles(minute.get("candles") or []) or to_decimal(pos["avg_price"])
        reason = overlay_actions(
            broker=broker,
            symbol=symbol,
            market=market,
            last_price=last,
            warnings=warnings,
            price_limits=limits,
            near_limit_pct=near,
            flatten_on_vi=flatten_vi,
            blocked_warnings=blocked,
        )
        if reason:
            actions.append(f"{symbol}:{reason}")
    return actions
