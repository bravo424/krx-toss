from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from krx_toss.execution.broker import Broker
from krx_toss.strategy.universe import vi_active
from krx_toss.toss.decimal_utils import maybe_decimal, round_to_tick

log = logging.getLogger(__name__)


def should_flatten_for_limit(last: Decimal, upper: Decimal | None, near_pct: Decimal) -> bool:
    if upper is None or last <= 0 or upper <= 0:
        return False
    return last >= upper * (Decimal("1") - near_pct)


def lock_stop_price(
    *,
    avg: Decimal,
    last: Decimal,
    current_stop: Decimal | None,
    take_profit: Decimal | None,
    lock_pct: Decimal,
    market: str,
) -> Decimal | None:
    """If last has reached +lock_pct, return a stop that locks that gain."""
    if lock_pct <= 0 or avg <= 0 or last <= 0:
        return None
    trigger = avg * (Decimal("1") + lock_pct)
    if last < trigger:
        return None
    if take_profit is not None and last >= take_profit:
        return None
    lock_px = round_to_tick(trigger, market, side="SELL")
    if take_profit is not None and lock_px >= take_profit:
        return None
    if current_stop is not None and current_stop >= lock_px:
        return None
    return lock_px


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
    lock_profit_pct: Decimal = Decimal("0"),
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
    avg = Decimal(pos["avg_price"])
    current_stop = Decimal(pos["stop_price"]) if pos.get("stop_price") else None
    stored_tp = Decimal(pos["take_profit_price"]) if pos.get("take_profit_price") else None
    desired_tp = round_to_tick(avg * (Decimal("1") + broker.limits.take_profit), market, side="SELL")
    tp = stored_tp
    if stored_tp is None or desired_tp > stored_tp:
        tp = desired_tp
    lock_px = lock_stop_price(
        avg=avg,
        last=last_price,
        current_stop=current_stop,
        take_profit=tp,
        lock_pct=lock_profit_pct,
        market=market,
    )
    raise_tp = stored_tp is None or (tp is not None and stored_tp is not None and tp > stored_tp)
    if lock_px is None and not raise_tp:
        return None
    stop = lock_px or current_stop
    if stop is None or tp is None:
        return None
    reason = "lock_profit" if lock_px is not None else "raise_tp"
    result = broker.replace_oco(symbol, stop=stop, take_profit=tp, reason=reason)
    if result is None:
        log.warning("OCO replace failed %s (%s)", symbol, reason)
        return None
    if lock_px is not None:
        try:
            broker.alerts.oco_locked(
                symbol=symbol,
                last_price=last_price,
                stop_price=lock_px,
                take_profit_price=tp,
                dry_run=broker.dry_run,
                name=broker.symbol_name(symbol),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("lock-profit telegram failed %s: %s", symbol, exc)
    return reason
