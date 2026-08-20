from __future__ import annotations

from decimal import Decimal
from typing import Any

from krx_toss.execution.broker import Broker
from krx_toss.strategy.universe import vi_active
from krx_toss.toss.decimal_utils import maybe_decimal


def should_flatten_for_limit(last: Decimal, upper: Decimal | None, near_pct: Decimal) -> bool:
    if upper is None or last <= 0 or upper <= 0:
        return False
    return last >= upper * (Decimal("1") - near_pct)


def overlay_actions(
    *,
    broker: Broker,
    symbol: str,
    market: str,
    last_price: Decimal,
    warnings: list[dict[str, Any]],
    price_limits: dict[str, Any],
    near_limit_pct: Decimal,
    flatten_on_vi: bool,
    blocked_warnings: set[str],
) -> str | None:
    pos = broker.blotter.position(symbol)
    if not pos:
        return None
    qty = int(pos["quantity"])
    if qty <= 0:
        return None
    types = {str(w.get("warningType") or "").upper() for w in warnings}
    if flatten_on_vi and vi_active(warnings):
        broker.flatten(symbol, market, last_price, qty, "vi_active")
        return "vi_active"
    if types & blocked_warnings:
        broker.flatten(symbol, market, last_price, qty, "warning")
        return "warning"
    upper = maybe_decimal(price_limits.get("upperLimitPrice"))
    if should_flatten_for_limit(last_price, upper, near_limit_pct):
        broker.flatten(symbol, market, last_price, qty, "near_upper_limit")
        return "near_upper_limit"
    return None
