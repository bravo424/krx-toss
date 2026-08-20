from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from krx_toss.execution.broker import Broker
from krx_toss.jobs.calendar import calendar_is_open
from krx_toss.jobs.open_entry import estimate_nav
from krx_toss.toss.client import TossClient
from krx_toss.toss.decimal_utils import to_decimal

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class SettlementFlow:
    settle_on: date
    amount: Decimal
    symbol: str
    side: str


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _money(payload: Any) -> Decimal:
    if payload is None:
        return Decimal("0")
    if isinstance(payload, dict):
        inner = payload.get("krw") or payload.get("KRW") or payload.get("amount") or payload.get("value")
        if inner is not None and not isinstance(inner, dict):
            return to_decimal(inner, default=Decimal("0"))
        return Decimal("0")
    return to_decimal(payload, default=Decimal("0"))


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


def settlement_date_of(
    order: dict[str, Any],
    *,
    fallback_sessions: Callable[[date, int], list[date]] | None = None,
) -> date | None:
    execution = order.get("execution") if isinstance(order.get("execution"), dict) else {}
    settle = _as_date(execution.get("settlementDate") or order.get("settlementDate"))
    if settle:
        return settle
    filled = _as_date(execution.get("filledAt") or order.get("filledAt") or order.get("orderedAt"))
    if filled is None:
        return None
    if fallback_sessions is None:
        return filled + timedelta(days=2)
    extra = fallback_sessions(filled, 2)
    return extra[-1] if extra else filled + timedelta(days=2)


def project_ladder(
    buying_power: Decimal,
    flows: list[SettlementFlow],
    t: date,
    t1: date,
    t2: date,
) -> dict[str, dict[str, Any]]:
    """T is spendable cash now. T+1/T+2 add SELL proceeds that settle on those dates.

    Buys already reduced buying power, so only future sell inflows are added.
    """
    by_day = {t: Decimal("0"), t1: Decimal("0"), t2: Decimal("0")}
    for flow in flows:
        if flow.amount <= 0:
            continue
        if flow.settle_on in by_day and flow.settle_on > t:
            by_day[flow.settle_on] += flow.amount
    cash_t = buying_power
    cash_t1 = cash_t + by_day[t1]
    cash_t2 = cash_t1 + by_day[t2]
    return {
        "T": {"date": t.isoformat(), "cash": cash_t, "inflow": by_day[t]},
        "T+1": {"date": t1.isoformat(), "cash": cash_t1, "inflow": by_day[t1]},
        "T+2": {"date": t2.isoformat(), "cash": cash_t2, "inflow": by_day[t2]},
    }


def _list_closed_orders(client: TossClient, start: date, end: date) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(8):
        payload = client.get_orders(
            "CLOSED",
            from_date=start.isoformat(),
            to_date=end.isoformat(),
            cursor=cursor,
            limit=100,
        )
        items, cursor = _order_items(payload)
        out.extend(items)
        if not cursor or not items:
            break
    return out


def _flows_from_orders(orders: list[dict[str, Any]], *, fallback) -> list[SettlementFlow]:
    flows: list[SettlementFlow] = []
    for order in orders:
        amount = net_cash_from_order(order)
        if amount == 0:
            continue
        settle = settlement_date_of(order, fallback_sessions=fallback)
        if settle is None:
            continue
        flows.append(
            SettlementFlow(
                settle_on=settle,
                amount=amount,
                symbol=str(order.get("symbol") or ""),
                side=str(order.get("side") or "").upper(),
            )
        )
    return flows


def settlement_snapshot(client: TossClient, broker: Broker, *, as_of: date | None = None) -> dict[str, Any]:
    today = as_of or datetime.now(KST).date()
    t1, t2 = next_session_days(client, today, count=2) if not broker.dry_run else (
        today + timedelta(days=1),
        today + timedelta(days=2),
    )
    buying_power = broker.buying_power_krw()
    lookback = today - timedelta(days=10)

    def fallback(start: date, count: int) -> list[date]:
        if broker.dry_run:
            return [start + timedelta(days=i + 1) for i in range(count)]
        return next_session_days(client, start, count=count)

    flows: list[SettlementFlow] = []
    if not broker.dry_run:
        try:
            orders = _list_closed_orders(client, lookback, t2)
            flows = _flows_from_orders(orders, fallback=fallback)
        except Exception as exc:  # noqa: BLE001
            log.warning("closed orders for settlement failed: %s", exc)
    if not flows:
        for row in broker.blotter.fills(limit=200):
            ts = _as_date(row.get("ts"))
            if ts is None:
                continue
            qty = int(row["quantity"])
            px = to_decimal(row["price"])
            side = str(row["side"]).upper()
            notional = px * qty
            amount = notional if side == "SELL" else -notional
            extra = fallback(ts, 2)
            settle = extra[-1] if extra else ts + timedelta(days=2)
            flows.append(SettlementFlow(settle_on=settle, amount=amount, symbol=str(row["symbol"]), side=side))

    ladder = project_ladder(buying_power, flows, today, t1, t2)
    pending = [
        {
            "date": flow.settle_on.isoformat(),
            "symbol": flow.symbol,
            "side": flow.side,
            "amount": flow.amount,
        }
        for flow in flows
        if flow.amount > 0 and flow.settle_on > today
    ]
    holdings_value = Decimal("0")
    try:
        holdings = client.get_holdings() if not broker.dry_run else {"items": broker.blotter.positions()}
        holdings_value = _money(holdings.get("marketValue") if isinstance(holdings, dict) else None)
        if holdings_value <= 0:
            for pos in broker.blotter.positions():
                holdings_value += to_decimal(pos["avg_price"]) * int(pos["quantity"])
    except Exception as exc:  # noqa: BLE001
        log.warning("holdings for settlement failed: %s", exc)
        for pos in broker.blotter.positions():
            holdings_value += to_decimal(pos["avg_price"]) * int(pos["quantity"])

    return {
        "as_of": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "currency": "KRW",
        "buying_power": buying_power,
        "holdings_value": holdings_value,
        "nav": estimate_nav(broker),
        "settlement": ladder,
        "pending_settlements": pending,
    }
