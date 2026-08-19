from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence

from krx_toss.toss.decimal_utils import maybe_decimal, to_decimal


@dataclass
class Candle:
    timestamp: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass
class FlowDay:
    date: str
    foreign_net: Decimal
    institution_net: Decimal


@dataclass
class CreditDay:
    date: str
    margin_balance: Decimal


def candle_price(row: dict[str, Any], *keys: str) -> Decimal:
    """Toss uses openPrice/closePrice; some payloads use open/close."""
    for key in keys:
        if row.get(key) not in (None, ""):
            return to_decimal(row[key])
    raise ValueError(f"missing price fields {keys}")


def parse_candles(payload: dict[str, Any] | list[dict[str, Any]]) -> list[Candle]:
    rows = payload if isinstance(payload, list) else payload.get("candles") or payload.get("items") or []
    candles: list[Candle] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            candles.append(
                Candle(
                    timestamp=str(row.get("timestamp") or row.get("date") or ""),
                    open=candle_price(row, "open", "openPrice", "o"),
                    high=candle_price(row, "high", "highPrice", "h"),
                    low=candle_price(row, "low", "lowPrice", "l"),
                    close=candle_price(row, "close", "closePrice", "c"),
                    volume=to_decimal(row.get("volume") or 0, default=Decimal("0")),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    candles.sort(key=lambda c: c.timestamp)
    return candles


def parse_flow(payload: dict[str, Any] | list[dict[str, Any]]) -> list[FlowDay]:
    rows = payload if isinstance(payload, list) else payload.get("records") or payload.get("items") or payload.get("investorTrading") or []
    out: list[FlowDay] = []
    for row in rows:
        foreign = row.get("foreigner") or {}
        institution = row.get("institution") or {}
        fnet = maybe_decimal(foreign.get("netBuyVolume"))
        inet = maybe_decimal(institution.get("netBuyVolume"))
        if fnet is None and inet is None:
            continue
        out.append(
            FlowDay(
                date=str(row.get("date") or ""),
                foreign_net=fnet or Decimal("0"),
                institution_net=inet or Decimal("0"),
            )
        )
    out.sort(key=lambda d: d.date)
    return out


def parse_credit(payload: dict[str, Any] | list[dict[str, Any]]) -> list[CreditDay]:
    rows = payload if isinstance(payload, list) else payload.get("records") or payload.get("items") or payload.get("creditTrades") or []
    out: list[CreditDay] = []
    for row in rows:
        margin = row.get("marginLoan") or {}
        balance = maybe_decimal(margin.get("balanceQuantity") or margin.get("balance") or margin.get("outstandingQuantity"))
        if balance is None:
            continue
        out.append(CreditDay(date=str(row.get("date") or ""), margin_balance=balance))
    out.sort(key=lambda d: d.date)
    return out


def sma(closes: Sequence[Decimal], window: int) -> Decimal | None:
    if window <= 0 or len(closes) < window:
        return None
    return sum(closes[-window:], Decimal("0")) / Decimal(window)


def period_return(closes: Sequence[Decimal], lookback: int) -> Decimal | None:
    if lookback <= 0 or len(closes) < lookback + 1:
        return None
    start = closes[-(lookback + 1)]
    if start == 0:
        return None
    return (closes[-1] / start) - 1


def net_flow_sum(days: Sequence[FlowDay], lookback: int, field: str) -> Decimal | None:
    if not days:
        return None
    window = days[-lookback:] if lookback else days
    total = Decimal("0")
    for day in window:
        total += getattr(day, field)
    return total


def credit_vs_average(days: Sequence[CreditDay], lookback: int) -> Decimal | None:
    if len(days) < 2:
        return None
    latest = days[-1].margin_balance
    hist = days[-lookback:-1] if lookback else days[:-1]
    if not hist:
        return None
    avg = sum((d.margin_balance for d in hist), Decimal("0")) / Decimal(len(hist))
    if avg == 0:
        return None
    return latest / avg
