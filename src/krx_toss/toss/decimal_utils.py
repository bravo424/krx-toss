from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Any


def to_decimal(value: Any, default: Decimal | None = None) -> Decimal:
    if value is None:
        if default is not None:
            return default
        raise ValueError("cannot convert None to Decimal")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("boolean is not a numeric amount")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    text = str(value).strip()
    if not text:
        if default is not None:
            return default
        raise ValueError("empty numeric string")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        if default is not None:
            return default
        raise ValueError(f"invalid decimal: {value!r}") from exc


def maybe_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return to_decimal(value)


def krx_tick_size(price: Decimal, market: str = "KOSPI") -> Decimal:
    """KRX price tick (원). Unified bands used for KOSPI/KOSDAQ common shares."""
    p = to_decimal(price)
    _ = market
    if p < Decimal("2000"):
        return Decimal("1")
    if p < Decimal("5000"):
        return Decimal("5")
    if p < Decimal("20000"):
        return Decimal("10")
    if p < Decimal("50000"):
        return Decimal("50")
    if p < Decimal("200000"):
        return Decimal("100")
    if p < Decimal("500000"):
        return Decimal("500")
    return Decimal("1000")


def round_to_tick(price: Decimal, market: str = "KOSPI", *, side: str = "BUY") -> Decimal:
    tick = krx_tick_size(price, market)
    if tick <= 0:
        return price
    ratio = to_decimal(price) / tick
    rounding = ROUND_DOWN if side.upper() == "BUY" else ROUND_HALF_UP
    snapped = (ratio.to_integral_value(rounding=rounding)) * tick
    if snapped <= 0:
        snapped = tick
    return snapped


def apply_tick_offset(price: Decimal, ticks: int, market: str = "KOSPI", *, side: str = "BUY") -> Decimal:
    base = round_to_tick(price, market, side=side)
    tick = krx_tick_size(base, market)
    return round_to_tick(base + tick * ticks, market, side=side)
