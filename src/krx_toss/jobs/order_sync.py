from __future__ import annotations

import json
import logging
from datetime import date
from decimal import Decimal
from typing import Any

from krx_toss.execution.broker import Broker
from krx_toss.toss.decimal_utils import to_decimal

log = logging.getLogger(__name__)

_FILLED = {"FILLED", "FILLED_ALL", "COMPLETE", "COMPLETED", "EXECUTED"}
_PARTIAL = {"PARTIAL", "PARTIALLY_FILLED", "PARTIAL_FILLED"}
_DONE = _FILLED | {"CANCELED", "CANCELLED", "REJECTED", "EXPIRED", "DAY_EXPIRED"}


def _extra(order: dict[str, Any]) -> dict[str, Any]:
    raw = order.get("extra")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _status(payload: dict[str, Any]) -> str:
    return str(payload.get("status") or payload.get("orderStatus") or "").upper()


def _quantity(payload: dict[str, Any], *keys: str, default: int = 0) -> int:
    for key in keys:
        if payload.get(key) is not None:
            try:
                return int(to_decimal(payload[key], default=Decimal("0")))
            except (ValueError, TypeError):
                continue
    return default


def _fill_price(payload: dict[str, Any], fallback: Decimal) -> Decimal:
    for key in ("averageFillPrice", "avgFillPrice", "avgPrice", "executedPrice", "fillPrice"):
        if payload.get(key) not in (None, ""):
            return to_decimal(payload[key], default=fallback)
    return fallback


def _holding_qty(item: dict[str, Any]) -> int:
    return _quantity(item, "quantity", "qty", "holdingQuantity", "sellableQuantity")


def sync_open_orders(broker: Broker) -> list[str]:
    """Poll SUBMITTED/PARTIAL live orders and record any new fills."""
    if broker.dry_run:
        return []
    events: list[str] = []
    for order in broker.blotter.pending_orders():
        order_id = order.get("order_id")
        if not order_id:
            continue
        extra = _extra(order)
        try:
            payload = broker.client.get_order(str(order_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("order status failed %s: %s", order_id, exc)
            continue
        if not isinstance(payload, dict):
            continue
        ordered_qty = int(order["quantity"])
        filled_qty = _quantity(payload, "filledQuantity", "executedQuantity", "cumQty", "filledQty")
        status = _status(payload)
        if status in _FILLED or (ordered_qty > 0 and filled_qty >= ordered_qty):
            status = "FILLED"
            filled_qty = max(filled_qty, ordered_qty)
        elif status in _PARTIAL or 0 < filled_qty < ordered_qty:
            status = "PARTIAL"
        elif status in _DONE:
            broker.blotter.set_order_status(str(order["client_order_id"]), status, extra)
            continue
        else:
            continue
        already = int(extra.get("alerted_filled_qty") or 0)
        delta = filled_qty - already
        extra["alerted_filled_qty"] = filled_qty
        if delta <= 0:
            broker.blotter.set_order_status(str(order["client_order_id"]), status, extra)
            continue
        price = _fill_price(payload, to_decimal(order["price"]))
        broker.apply_remote_fill(
            client_order_id=str(order["client_order_id"]),
            symbol=str(order["symbol"]),
            side=str(order["side"]),
            quantity=delta,
            price=price,
            market=str(extra.get("market") or "KOSPI"),
            status=status,
            extra=extra,
        )
        events.append(f"{order['symbol']}:{order['side']}:{status}:{delta}")
        log.info("fill %s %s qty=%s px=%s", order["symbol"], order["side"], delta, price)
    return events


def reconcile_holdings(broker: Broker, skip_symbols: set[str] | None = None) -> list[str]:
    """Treat blotter names missing from live holdings as OCO/external sells."""
    if broker.dry_run:
        return []
    skip = set(skip_symbols or ())
    skip.update(str(o["symbol"]) for o in broker.blotter.pending_orders())
    try:
        items = broker.sync_from_holdings()
    except Exception as exc:  # noqa: BLE001
        log.warning("holdings sync failed: %s", exc)
        return []
    held: dict[str, int] = {}
    for item in items:
        symbol = str(item.get("symbol") or "")
        if not symbol:
            continue
        held[symbol] = _holding_qty(item)
    events: list[str] = []
    for pos in list(broker.blotter.positions()):
        symbol = pos["symbol"]
        if symbol in skip:
            continue
        blotter_qty = int(pos["quantity"])
        live_qty = held.get(symbol, 0)
        if live_qty >= blotter_qty:
            continue
        sold = blotter_qty - live_qty
        market = pos.get("market") or "KOSPI"
        entry = Decimal(pos["avg_price"])
        try:
            prices = broker.client.get_prices([symbol])
            last = to_decimal((prices[0] if prices else {}).get("lastPrice") or entry)
        except Exception:  # noqa: BLE001
            last = entry
        pnl = broker.cost.net_pnl(entry * sold, last * sold, market)
        broker.blotter.add_realized(date.today(), pnl)
        broker.blotter.add_fill(symbol, "SELL", sold, last)
        broker.blotter.upsert_position(
            symbol,
            live_qty,
            entry,
            market,
            pos.get("opened_on") or date.today().isoformat(),
            None if live_qty <= 0 else pos.get("oco_id"),
            None if live_qty <= 0 else (Decimal(pos["stop_price"]) if pos.get("stop_price") else None),
            None if live_qty <= 0 else (Decimal(pos["take_profit_price"]) if pos.get("take_profit_price") else None),
        )
        broker.alerts.order_filled(
            symbol=symbol,
            side="SELL",
            quantity=sold,
            price=last,
            dry_run=False,
            reason="oco_or_external",
            pnl=pnl,
            entry_price=entry,
            name=broker.symbol_name(symbol),
        )
        events.append(f"{symbol}:SELL:reconcile:{sold}")
        log.info("holdings reconcile %s sold=%s px=%s", symbol, sold, last)
    return events


def attach_missing_ocos(broker: Broker) -> list[str]:
    """Place TP/SL OCO only for names we actually hold."""
    events: list[str] = []
    for pos in broker.blotter.positions():
        if pos.get("oco_id"):
            continue
        symbol = str(pos["symbol"])
        try:
            result = broker.ensure_oco(symbol)
        except Exception as exc:  # noqa: BLE001
            log.warning("OCO attach failed %s: %s", symbol, exc)
            continue
        if result:
            events.append(f"{symbol}:OCO")
    return events


def sync_fills(broker: Broker) -> list[str]:
    events = sync_open_orders(broker)
    skip = {e.split(":", 1)[0] for e in events if ":BUY:" in e}
    events.extend(reconcile_holdings(broker, skip_symbols=skip))
    events.extend(attach_missing_ocos(broker))
    return events
