from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from krx_toss.execution.broker import Broker
from krx_toss.jobs.calendar import calendar_is_open
from krx_toss.toss.client import TossClient
from krx_toss.toss.decimal_utils import to_decimal

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


def _money(payload: Any) -> Decimal:
    """KRW from a Toss money field: bare decimal or nested ``{amount:{krw}}`` / ``{krw}``."""
    if payload is None or payload == "":
        return Decimal("0")
    if isinstance(payload, dict):
        if payload.get("krw") is not None or payload.get("KRW") is not None:
            return to_decimal(payload.get("krw") or payload.get("KRW"), default=Decimal("0"))
        for key in ("amount", "cashBuyingPower", "value", "cash"):
            if payload.get(key) is not None:
                got = _money(payload.get(key))
                if got:
                    return got
        return Decimal("0")
    return to_decimal(payload, default=Decimal("0"))


def holdings_market_value_krw(holdings: dict[str, Any] | None) -> Decimal:
    if not holdings:
        return Decimal("0")
    overview = holdings.get("marketValue")
    if isinstance(overview, dict):
        value = _money(overview.get("amount") if overview.get("amount") is not None else overview)
        if value > 0:
            return value
    total = Decimal("0")
    for item in holdings.get("items") or []:
        if not isinstance(item, dict):
            continue
        mv = item.get("marketValue")
        if isinstance(mv, dict):
            piece = _money(mv.get("amount") if mv.get("amount") is not None else mv)
        else:
            piece = _money(mv)
        if piece <= 0:
            qty = to_decimal(item.get("quantity") or 0, default=Decimal("0"))
            last = to_decimal(item.get("lastPrice") or 0, default=Decimal("0"))
            piece = qty * last
        total += piece
    return total


def holdings_as_positions(
    holdings: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Decimal], dict[str, str]]:
    positions: list[dict[str, Any]] = []
    marks: dict[str, Decimal] = {}
    names: dict[str, str] = {}
    for item in (holdings or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "")
        qty = int(to_decimal(item.get("quantity") or 0, default=Decimal("0")))
        if not symbol or qty <= 0:
            continue
        avg = to_decimal(item.get("averagePurchasePrice") or 0, default=Decimal("0"))
        last = to_decimal(item.get("lastPrice") or 0, default=Decimal("0"))
        positions.append({"symbol": symbol, "quantity": qty, "avg_price": str(avg), "name": item.get("name") or ""})
        if last > 0:
            marks[symbol] = last
        if item.get("name"):
            names[symbol] = str(item["name"])
    return positions, marks, names


def reserved_buy_notional(orders: list[dict[str, Any]]) -> Decimal:
    """Cash locked in unfilled BUY orders — Toss subtracts this from cashBuyingPower."""
    total = Decimal("0")
    for order in orders:
        if str(order.get("side") or "").upper() != "BUY":
            continue
        qty = to_decimal(order.get("quantity") or 0, default=Decimal("0"))
        execution = order.get("execution") if isinstance(order.get("execution"), dict) else {}
        filled = to_decimal(execution.get("filledQuantity") or 0, default=Decimal("0"))
        remaining = qty - filled
        if remaining <= 0:
            continue
        px = to_decimal(order.get("price") or 0, default=Decimal("0"))
        if px <= 0:
            continue
        total += remaining * px
    return total


def _order_items(payload: dict[str, Any] | list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)], None
    items = payload.get("items") or payload.get("orders") or payload.get("list") or []
    cursor = payload.get("nextCursor") or payload.get("cursor") or payload.get("next")
    if isinstance(items, list):
        return [row for row in items if isinstance(row, dict)], str(cursor) if cursor else None
    return [], None


_SESSION_DAY_CACHE: dict[str, bool] = {}


def _is_session_day(client: TossClient, day: date) -> bool:
    key = day.isoformat()
    cached = _SESSION_DAY_CACHE.get(key)
    if cached is not None:
        return cached
    if day.weekday() >= 5:
        _SESSION_DAY_CACHE[key] = False
        return False
    try:
        cal = client.get_kr_calendar(day.isoformat())
    except Exception:  # noqa: BLE001
        result = day.weekday() < 5
        _SESSION_DAY_CACHE[key] = result
        return result
    result = calendar_is_open(cal, datetime(day.year, day.month, day.day, tzinfo=KST))
    if len(_SESSION_DAY_CACHE) >= 64:
        _SESSION_DAY_CACHE.pop(next(iter(_SESSION_DAY_CACHE)))
    _SESSION_DAY_CACHE[key] = result
    return result


def next_session_days(client: TossClient, start: date, count: int = 2) -> list[date]:
    found: list[date] = []
    cursor = start
    for _ in range(21):
        if len(found) >= count:
            break
        cursor += timedelta(days=1)
        if _is_session_day(client, cursor):
            found.append(cursor)
    while len(found) < count:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            found.append(cursor)
    return found[:count]


def net_cash_from_order(order: dict[str, Any]) -> Decimal:
    execution = order.get("execution") if isinstance(order.get("execution"), dict) else {}
    filled_qty = to_decimal(execution.get("filledQuantity") or order.get("filledQuantity") or 0, default=Decimal("0"))
    if filled_qty <= 0:
        return Decimal("0")
    filled_amt = to_decimal(execution.get("filledAmount") or 0, default=Decimal("0"))
    avg = to_decimal(execution.get("averageFilledPrice") or order.get("price") or 0, default=Decimal("0"))
    if filled_amt <= 0 and avg > 0:
        filled_amt = filled_qty * avg
    fee = to_decimal(execution.get("commission") or 0, default=Decimal("0"))
    tax = to_decimal(execution.get("tax") or 0, default=Decimal("0"))
    side = str(order.get("side") or "").upper()
    if side == "SELL":
        return filled_amt - fee - tax
    if side == "BUY":
        return -(filled_amt + fee)
    return Decimal("0")


def project_ladder(
    available: Decimal,
    t: date,
    t1: date,
    t2: date,
) -> dict[str, dict[str, Any]]:
    """T/T+1/T+2 are Toss available cash (주문가능 + open-buy reserve).

    ``cashBuyingPower`` already includes unsettled sell proceeds, so those
    fills must not be added again.
    """
    cash = available
    zero = Decimal("0")
    return {
        "T": {"date": t.isoformat(), "cash": cash, "inflow": zero},
        "T+1": {"date": t1.isoformat(), "cash": cash, "inflow": zero},
        "T+2": {"date": t2.isoformat(), "cash": cash, "inflow": zero},
    }


def settlement_snapshot(client: TossClient, broker: Broker, *, as_of: date | None = None) -> dict[str, Any]:
    today = as_of or datetime.now(KST).date()
    t1, t2 = next_session_days(client, today, count=2) if not broker.dry_run else (
        today + timedelta(days=1),
        today + timedelta(days=2),
    )
    buying_power = broker.buying_power_krw()
    reserved = Decimal("0")
    holdings: dict[str, Any] = {}
    if not broker.dry_run:
        try:
            open_items, _cursor = _order_items(client.get_orders("OPEN", limit=100))
            reserved = reserved_buy_notional(open_items)
        except Exception as exc:  # noqa: BLE001
            log.warning("open orders for cash snapshot failed: %s", exc)
        try:
            holdings = client.get_holdings()
        except Exception as exc:  # noqa: BLE001
            log.warning("holdings for settlement failed: %s", exc)
    else:
        holdings = {"items": broker.blotter.positions()}

    available = buying_power + reserved
    holdings_value = holdings_market_value_krw(holdings)
    if holdings_value <= 0:
        for pos in broker.blotter.positions():
            holdings_value += to_decimal(pos.get("avg_price") or 0) * int(pos["quantity"])
    live_positions, live_marks, live_names = holdings_as_positions(holdings)
    ladder = project_ladder(available, today, t1, t2)
    log.info(
        "balance snapshot buying_power=%s reserved_buys=%s available=%s stocks=%s",
        buying_power,
        reserved,
        available,
        holdings_value,
    )
    return {
        "as_of": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "currency": "KRW",
        "buying_power": buying_power,
        "reserved_buys": reserved,
        "available": available,
        "holdings_value": holdings_value,
        "positions": live_positions,
        "marks": live_marks,
        "names": live_names,
        "nav": holdings_value + available,
        "settlement": ladder,
        "pending_settlements": [],
    }
