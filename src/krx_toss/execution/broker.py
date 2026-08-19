from __future__ import annotations

import logging
import threading
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from krx_toss.alerts import TradingAlerts
from krx_toss.cost.model import CostModel
from krx_toss.execution.blotter import Blotter
from krx_toss.execution.kill_switch import KillSwitch
from krx_toss.strategy.risk import OrderIntent, RiskLimits
from krx_toss.toss.client import TossClient
from krx_toss.toss.decimal_utils import round_to_tick, to_decimal
from krx_toss.toss.errors import TossApiError

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


class Broker:
    def __init__(
        self,
        client: TossClient,
        blotter: Blotter,
        kill_switch: KillSwitch,
        *,
        dry_run: bool,
        cost: CostModel,
        limits: RiskLimits,
        alerts: TradingAlerts | None = None,
    ) -> None:
        self.client = client
        self.blotter = blotter
        self.kill_switch = kill_switch
        self.dry_run = dry_run
        self.cost = cost
        self.limits = limits
        self.alerts = alerts or TradingAlerts()
        self._symbol_locks: dict[str, threading.Lock] = {}
        self._map_lock = threading.Lock()
        self._name_cache: dict[str, str] = {}

    def _lock_for(self, symbol: str) -> threading.Lock:
        with self._map_lock:
            lock = self._symbol_locks.get(symbol)
            if lock is None:
                lock = threading.Lock()
                self._symbol_locks[symbol] = lock
            return lock

    def symbol_name(self, symbol: str) -> str:
        cached = self._name_cache.get(symbol)
        if cached is not None:
            return cached
        name = ""
        try:
            rows = self.client.get_stocks([symbol])
            for row in rows or []:
                if str(row.get("symbol") or "") == symbol or not name:
                    name = str(row.get("name") or row.get("koreanName") or row.get("stockName") or "")
                    if str(row.get("symbol") or "") == symbol and name:
                        break
        except Exception as exc:  # noqa: BLE001
            log.warning("stock name failed %s: %s", symbol, exc)
        self._name_cache[symbol] = name
        return name

    def client_order_id(self) -> str:
        return uuid.uuid4().hex[:32]

    def submit_limit(self, intent: OrderIntent) -> dict[str, Any]:
        if self.kill_switch.tripped():
            raise TossApiError("kill switch is tripped", code="account-restricted")
        body = {
            "clientOrderId": self.client_order_id(),
            "symbol": intent.symbol,
            "side": intent.side,
            "orderType": "LIMIT",
            "timeInForce": "DAY",
            "quantity": str(intent.quantity),
            "price": str(int(intent.price)),
            "confirmHighValueOrder": bool(intent.confirm_high_value),
        }
        extra_meta = {
            "market": intent.market,
            "intent": body,
            "stop_price": str(intent.stop_price),
            "take_profit_price": str(intent.take_profit_price),
        }
        with self._lock_for(intent.symbol):
            if self.dry_run:
                log.info("DRY RUN order %s", body)
                self.blotter.record_order(
                    client_order_id=body["clientOrderId"],
                    order_id=None,
                    symbol=intent.symbol,
                    side=intent.side,
                    quantity=intent.quantity,
                    price=intent.price,
                    status="DRY_RUN",
                    dry_run=True,
                    extra=extra_meta,
                )
                self.alerts.order_placed(
                    symbol=intent.symbol,
                    side=intent.side,
                    quantity=intent.quantity,
                    price=intent.price,
                    dry_run=True,
                    order_id=f"dry-{body['clientOrderId']}",
                    stop_price=intent.stop_price,
                    take_profit_price=intent.take_profit_price,
                    name=self.symbol_name(intent.symbol),
                )
                if intent.side == "BUY":
                    self.blotter.upsert_position(
                        intent.symbol,
                        intent.quantity,
                        intent.price,
                        intent.market,
                        date.today().isoformat(),
                        None,
                        intent.stop_price,
                        intent.take_profit_price,
                    )
                    self.blotter.add_fill(intent.symbol, "BUY", intent.quantity, intent.price)
                    self.alerts.order_filled(
                        symbol=intent.symbol,
                        side="BUY",
                        quantity=intent.quantity,
                        price=intent.price,
                        dry_run=True,
                        name=self.symbol_name(intent.symbol),
                    )
                return {"orderId": f"dry-{body['clientOrderId']}", "clientOrderId": body["clientOrderId"], "dryRun": True}
            result = self.client.create_order(body)
            order_id = str(result.get("orderId") or "")
            self.blotter.record_order(
                client_order_id=body["clientOrderId"],
                order_id=order_id,
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                price=intent.price,
                status="SUBMITTED",
                dry_run=False,
                extra={**result, **extra_meta},
            )
            self.alerts.order_placed(
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                price=intent.price,
                dry_run=False,
                order_id=order_id or body["clientOrderId"],
                stop_price=intent.stop_price,
                take_profit_price=intent.take_profit_price,
                name=self.symbol_name(intent.symbol),
            )
            return result

    @staticmethod
    def _payload_qty(payload: dict[str, Any] | None) -> int:
        if not isinstance(payload, dict):
            return 0
        for key in ("sellableQuantity", "quantity", "availableQuantity", "sellable", "qty"):
            if payload.get(key) not in (None, ""):
                try:
                    return int(to_decimal(payload[key], default=Decimal("0")))
                except (ValueError, TypeError, ArithmeticError):
                    continue
        return 0

    def live_sellable_qty(self, symbol: str) -> int:
        if self.dry_run:
            pos = self.blotter.position(symbol)
            return int(pos["quantity"]) if pos else 0
        qty = 0
        try:
            qty = max(qty, self._payload_qty(self.client.get_sellable_quantity(symbol)))
        except Exception as exc:  # noqa: BLE001
            log.warning("sellable-quantity failed %s: %s", symbol, exc)
        if qty > 0:
            return qty
        try:
            holdings = self.client.get_holdings()
            for item in holdings.get("items") or []:
                if str(item.get("symbol") or "") == symbol:
                    return max(
                        self._payload_qty(item),
                        int(to_decimal(item.get("holdingQuantity") or 0, default=Decimal("0"))),
                    )
        except Exception as exc:  # noqa: BLE001
            log.warning("holdings lookup for OCO failed %s: %s", symbol, exc)
        return 0

    def _remember_oco(self, symbol: str, market: str, oco_id: str, stop: Decimal, take_profit: Decimal) -> None:
        pos = self.blotter.position(symbol)
        if not pos:
            return
        self.blotter.upsert_position(
            symbol,
            int(pos["quantity"]),
            Decimal(pos["avg_price"]),
            pos.get("market") or market,
            pos.get("opened_on") or date.today().isoformat(),
            oco_id,
            stop,
            take_profit,
        )

    def attach_oco(self, intent: OrderIntent, quantity: int | None = None) -> dict[str, Any] | None:
        qty = quantity or intent.quantity
        if qty <= 0:
            return None
        if not self.dry_run:
            sellable = self.live_sellable_qty(intent.symbol)
            if sellable <= 0:
                log.info("skip OCO %s: no sellable shares yet (buy unfilled)", intent.symbol)
                return None
            qty = min(qty, sellable)
        expire = (datetime.now(KST).date() + timedelta(days=7)).isoformat()
        body = {
            "symbol": intent.symbol,
            "type": "OCO",
            "quantity": str(qty),
            "orderType": "LIMIT",
            "clientOrderId": self.client_order_id(),
            "expireDate": expire,
            "confirmHighValueOrder": (intent.take_profit_price * qty) >= self.limits.high_value_threshold,
            "first": {
                "orderSide": "SELL",
                "triggerPrice": str(int(intent.take_profit_price)),
                "orderPrice": str(int(intent.take_profit_price)),
            },
            "second": {
                "orderSide": "SELL",
                "triggerPrice": str(int(intent.stop_price)),
                "orderPrice": str(int(intent.stop_price)),
            },
        }
        if self.dry_run:
            log.info("DRY RUN OCO %s", body)
            oco_id = f"dry-oco-{body['clientOrderId']}"
            self._remember_oco(intent.symbol, intent.market, oco_id, intent.stop_price, intent.take_profit_price)
            return {"conditionalOrderId": oco_id, "dryRun": True}
        try:
            result = self.client.create_conditional_order(body)
        except TossApiError as exc:
            log.warning("OCO rejected %s: %s", intent.symbol, exc)
            return None
        oco_id = str(result.get("conditionalOrderId") or "")
        self._remember_oco(intent.symbol, intent.market, oco_id, intent.stop_price, intent.take_profit_price)
        log.info("OCO attached %s qty=%s tp=%s sl=%s", intent.symbol, qty, intent.take_profit_price, intent.stop_price)
        return result

    def ensure_oco(self, symbol: str) -> dict[str, Any] | None:
        pos = self.blotter.position(symbol)
        if not pos or int(pos["quantity"]) <= 0:
            return None
        if pos.get("oco_id"):
            return None
        market = pos.get("market") or "KOSPI"
        avg = Decimal(pos["avg_price"])
        qty = int(pos["quantity"])
        stop = (
            Decimal(pos["stop_price"])
            if pos.get("stop_price")
            else round_to_tick(avg * (Decimal("1") - self.limits.stop_loss), market, side="SELL")
        )
        take_profit = (
            Decimal(pos["take_profit_price"])
            if pos.get("take_profit_price")
            else round_to_tick(avg * (Decimal("1") + self.limits.take_profit), market, side="SELL")
        )
        intent = OrderIntent(
            symbol=symbol,
            market=market,
            side="SELL",
            quantity=qty,
            price=avg,
            confirm_high_value=(take_profit * qty) >= self.limits.high_value_threshold,
            notional=avg * qty,
            stop_price=stop,
            take_profit_price=take_profit,
        )
        return self.attach_oco(intent, quantity=qty)

    def flatten(self, symbol: str, market: str, price: Decimal, quantity: int, reason: str) -> dict[str, Any]:
        px = round_to_tick(price, market, side="SELL")
        intent = OrderIntent(
            symbol=symbol,
            market=market,
            side="SELL",
            quantity=quantity,
            price=px,
            confirm_high_value=(px * quantity) >= self.limits.high_value_threshold,
            notional=px * quantity,
            stop_price=px,
            take_profit_price=px,
        )
        log.warning("flatten %s qty=%s reason=%s", symbol, quantity, reason)
        pos = self.blotter.position(symbol)
        if pos and pos.get("oco_id") and not str(pos["oco_id"]).startswith("dry-") and not self.dry_run:
            try:
                self.client.cancel_conditional_order(str(pos["oco_id"]))
            except TossApiError as exc:
                log.warning("cancel OCO failed: %s", exc)
        result = self.submit_limit(intent)
        if pos:
            buy_notional = Decimal(pos["avg_price"]) * quantity
            sell_notional = px * quantity
            pnl = self.cost.net_pnl(buy_notional, sell_notional, market)
            self.blotter.add_realized(date.today(), pnl)
            self.blotter.add_fill(symbol, "SELL", quantity, px)
            remaining = int(pos["quantity"]) - quantity
            self.blotter.upsert_position(
                symbol,
                remaining,
                Decimal(pos["avg_price"]),
                market,
                pos.get("opened_on") or date.today().isoformat(),
                None,
                None,
                None,
            )
            client_order_id = str(result.get("clientOrderId") or "")
            if client_order_id:
                self.blotter.set_order_status(client_order_id, "FILLED")
            self.alerts.order_filled(
                symbol=symbol,
                side="SELL",
                quantity=quantity,
                price=px,
                dry_run=self.dry_run,
                reason=reason,
                pnl=pnl,
                entry_price=Decimal(pos["avg_price"]),
                name=self.symbol_name(symbol),
            )
        return result

    def sync_from_holdings(self) -> list[dict[str, Any]]:
        if self.dry_run:
            return self.blotter.positions()
        holdings = self.client.get_holdings()
        items = holdings.get("items") or []
        return items

    def buying_power_krw(self) -> Decimal:
        if self.dry_run:
            return Decimal("100000000")
        payload = self.client.get_buying_power()
        krw = (
            payload.get("cashBuyingPower")
            or payload.get("krw")
            or payload.get("buyingPower")
            or payload
        )
        if isinstance(krw, dict):
            value = krw.get("krw") or krw.get("cash") or krw.get("available") or 0
            return to_decimal(value, default=Decimal("0"))
        return to_decimal(krw, default=Decimal("0"))

    def apply_remote_fill(
        self,
        *,
        client_order_id: str,
        symbol: str,
        side: str,
        quantity: int,
        price: Decimal,
        market: str,
        status: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Apply a live fill discovered from order-status polling."""
        extra = extra or {}
        stop = to_decimal(extra["stop_price"]) if extra.get("stop_price") else None
        take_profit = to_decimal(extra["take_profit_price"]) if extra.get("take_profit_price") else None
        if quantity <= 0:
            self.blotter.set_order_status(client_order_id, status, extra)
            return
        side_u = side.upper()
        pos = self.blotter.position(symbol)
        if side_u == "BUY":
            if pos:
                old_qty = int(pos["quantity"])
                old_px = Decimal(pos["avg_price"])
                new_qty = old_qty + quantity
                avg = ((old_px * old_qty) + (price * quantity)) / new_qty
                self.blotter.upsert_position(
                    symbol,
                    new_qty,
                    avg,
                    pos.get("market") or market,
                    pos.get("opened_on") or date.today().isoformat(),
                    pos.get("oco_id"),
                    Decimal(pos["stop_price"]) if pos.get("stop_price") else stop,
                    Decimal(pos["take_profit_price"]) if pos.get("take_profit_price") else take_profit,
                )
            else:
                self.blotter.upsert_position(
                    symbol,
                    quantity,
                    price,
                    market,
                    date.today().isoformat(),
                    None,
                    stop,
                    take_profit,
                )
            self.blotter.add_fill(symbol, "BUY", quantity, price)
            self.blotter.set_order_status(client_order_id, status, extra)
            self.alerts.order_filled(
                symbol=symbol,
                side="BUY",
                quantity=quantity,
                price=price,
                dry_run=False,
                name=self.symbol_name(symbol),
            )
            return
        entry = Decimal(pos["avg_price"]) if pos else price
        close_qty = quantity
        if pos:
            close_qty = min(quantity, int(pos["quantity"]))
            remaining = int(pos["quantity"]) - close_qty
            pnl = self.cost.net_pnl(entry * close_qty, price * close_qty, pos.get("market") or market)
            self.blotter.add_realized(date.today(), pnl)
            self.blotter.add_fill(symbol, "SELL", close_qty, price)
            self.blotter.upsert_position(
                symbol,
                remaining,
                entry,
                pos.get("market") or market,
                pos.get("opened_on") or date.today().isoformat(),
                None if remaining <= 0 else pos.get("oco_id"),
                None if remaining <= 0 else (Decimal(pos["stop_price"]) if pos.get("stop_price") else None),
                None if remaining <= 0 else (Decimal(pos["take_profit_price"]) if pos.get("take_profit_price") else None),
            )
        else:
            pnl = None
            self.blotter.add_fill(symbol, "SELL", close_qty, price)
        self.blotter.set_order_status(client_order_id, status, extra)
        self.alerts.order_filled(
            symbol=symbol,
            side="SELL",
            quantity=close_qty,
            price=price,
            dry_run=False,
            reason="fill",
            pnl=pnl,
            entry_price=entry if pos else None,
            name=self.symbol_name(symbol),
        )
