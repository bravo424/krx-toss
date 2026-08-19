from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from decimal import ROUND_DOWN, Decimal
from zoneinfo import ZoneInfo

from krx_toss.toss.decimal_utils import krx_tick_size, round_to_tick, to_decimal

KST = ZoneInfo("Asia/Seoul")
HIGH_VALUE = Decimal("100000000")
MAJOR_SHAREHOLDER_NOTIONAL = Decimal("5000000000")


@dataclass(frozen=True)
class RiskLimits:
    max_positions: int
    position_nav_pct: Decimal
    cash_buffer_pct: Decimal
    per_name_risk_pct: Decimal
    daily_loss_kill_pct: Decimal
    max_notional_per_name: Decimal
    kospi_ownership_pct: Decimal
    kosdaq_ownership_pct: Decimal
    high_value_threshold: Decimal
    stop_loss: Decimal
    take_profit: Decimal

    @classmethod
    def from_strategy(cls, strategy: dict) -> RiskLimits:
        risk = strategy.get("risk") or {}
        exit_cfg = strategy.get("exit") or {}
        return cls(
            max_positions=int(risk.get("max_positions", 8)),
            position_nav_pct=to_decimal(risk.get("position_nav_pct", "0.10")),
            cash_buffer_pct=to_decimal(risk.get("cash_buffer_pct", "0.20")),
            per_name_risk_pct=to_decimal(risk.get("per_name_risk_pct", "0.02")),
            daily_loss_kill_pct=to_decimal(risk.get("daily_loss_kill_pct", "0.02")),
            max_notional_per_name=to_decimal(risk.get("max_notional_per_name", MAJOR_SHAREHOLDER_NOTIONAL)),
            kospi_ownership_pct=to_decimal(risk.get("kospi_ownership_pct", "0.01")),
            kosdaq_ownership_pct=to_decimal(risk.get("kosdaq_ownership_pct", "0.02")),
            high_value_threshold=to_decimal(risk.get("high_value_threshold", HIGH_VALUE)),
            stop_loss=to_decimal(exit_cfg.get("stop_loss", "0.04")),
            take_profit=to_decimal(exit_cfg.get("take_profit", "0.06")),
        )


@dataclass
class OrderIntent:
    symbol: str
    market: str
    side: str
    quantity: int
    price: Decimal
    confirm_high_value: bool
    notional: Decimal
    stop_price: Decimal
    take_profit_price: Decimal


def parse_hhmm(value: str | None, default: str = "09:00") -> time:
    """Parse clock times from YAML or Toss calendar fields.

    Accepts ``HH:MM``, ``HH:MM:SS``, ``HHMM``, a bare hour, or an ISO datetime.
    Invalid values fall back to ``default`` instead of crashing the scheduler.
    """
    text = str(value or "").strip()
    parsed = _parse_hhmm_raw(text)
    if parsed is not None:
        return parsed
    fallback = _parse_hhmm_raw(str(default).strip())
    if fallback is not None:
        return fallback
    return time(9, 0)


def _parse_hhmm_raw(text: str) -> time | None:
    if not text:
        return None
    if "T" in text:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone(KST)
            return time(dt.hour, dt.minute)
        except ValueError:
            pass
    if ":" in text:
        parts = text.split(":")
        try:
            hour = int("".join(ch for ch in parts[0] if ch.isdigit()) or 0)
            minute = int("".join(ch for ch in parts[1] if ch.isdigit())[:2] or 0)
            return time(hour % 24, minute % 60)
        except (ValueError, IndexError):
            return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 4:
        try:
            return time(int(digits[:2]) % 24, int(digits[2:]) % 60)
        except ValueError:
            return None
    if len(digits) in {1, 2}:
        try:
            return time(int(digits) % 24, 0)
        except ValueError:
            return None
    return None


def now_kst(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(KST)
    if now.tzinfo is None:
        # Naive values are this machine's wall clock (often UTC+8), not KST.
        now = now.astimezone()
    return now.astimezone(KST)


def in_window(now: datetime, start: str, end: str) -> bool:
    clock = now_kst(now).time()
    return parse_hhmm(start) <= clock < parse_hhmm(end)


def entries_allowed(now: datetime, no_new_until: str = "09:15") -> bool:
    return now_kst(now).time() >= parse_hhmm(no_new_until)


def daily_loss_breached(nav: Decimal, daily_pnl: Decimal, limits: RiskLimits) -> bool:
    if nav <= 0:
        return True
    return daily_pnl <= -(nav * limits.daily_loss_kill_pct)


def ownership_cap_shares(shares_outstanding: Decimal | None, market: str, limits: RiskLimits) -> Decimal | None:
    if not shares_outstanding or shares_outstanding <= 0:
        return None
    pct = limits.kosdaq_ownership_pct if str(market).upper() == "KOSDAQ" else limits.kospi_ownership_pct
    return (shares_outstanding * pct).to_integral_value(rounding=ROUND_DOWN)


def size_buy(
    *,
    nav: Decimal,
    price: Decimal,
    market: str,
    open_positions: int,
    shares_outstanding: Decimal | None,
    limits: RiskLimits,
) -> OrderIntent | None:
    if open_positions >= limits.max_positions:
        return None
    if price <= 0 or nav <= 0:
        return None
    budget = nav * limits.position_nav_pct
    max_from_buffer = nav * (Decimal("1") - limits.cash_buffer_pct)
    budget = min(budget, max_from_buffer, limits.max_notional_per_name)
    stop = round_to_tick(price * (Decimal("1") - limits.stop_loss), market, side="SELL")
    risk_per_share = price - stop
    if risk_per_share <= 0:
        risk_per_share = krx_tick_size(price, market)
    max_risk_notional = nav * limits.per_name_risk_pct
    qty_risk = int(max_risk_notional / risk_per_share)
    qty_budget = int(budget / price)
    qty = min(qty_risk, qty_budget)
    cap = ownership_cap_shares(shares_outstanding, market, limits)
    if cap is not None:
        qty = min(qty, int(cap))
    if qty <= 0:
        return None
    px = round_to_tick(price, market, side="BUY")
    notional = px * qty
    tp = round_to_tick(px * (Decimal("1") + (limits.stop_loss * Decimal("1.5"))), market, side="SELL")
    return OrderIntent(
        symbol="",
        market=market,
        side="BUY",
        quantity=qty,
        price=px,
        confirm_high_value=notional >= limits.high_value_threshold,
        notional=notional,
        stop_price=stop,
        take_profit_price=tp,
    )


def attach_symbol(intent: OrderIntent, symbol: str, take_profit: Decimal) -> OrderIntent:
    tp = round_to_tick(intent.price * (Decimal("1") + take_profit), intent.market, side="SELL")
    return OrderIntent(
        symbol=symbol,
        market=intent.market,
        side=intent.side,
        quantity=intent.quantity,
        price=intent.price,
        confirm_high_value=intent.confirm_high_value,
        notional=intent.notional,
        stop_price=intent.stop_price,
        take_profit_price=tp,
    )
