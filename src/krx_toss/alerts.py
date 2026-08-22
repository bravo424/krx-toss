from __future__ import annotations

import html
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from krx_toss.toss.decimal_utils import to_decimal

KST = ZoneInfo("Asia/Seoul")
STRATEGY = "krx-toss"


class MessageSender(Protocol):
    def send(self, text: str) -> None: ...


def _sgn(value: Decimal, digits: int = 0) -> str:
    number = f"{value:,.{digits}f}"
    return f"+{number}" if value >= 0 else number


def _pnl_emoji(value: Decimal) -> str:
    return "🟢" if value >= 0 else "🔴"


def _krw(value: Decimal | int | str) -> str:
    return f"₩{to_decimal(value, default=Decimal('0')):,.0f}"


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=False)


def _symbol_label(symbol: str, name: str | None = None) -> str:
    label = _esc(symbol)
    if name:
        label = f"{label} {_esc(name)}"
    return label


def _dry_suffix(dry_run: bool) -> str:
    return "  [DRY RUN]" if dry_run else ""


class TradingAlerts:
    """Two-bot split copied from the crypto workspace.

    * ``trade`` (nasang_bot) — order placements and fills.
    * ``position`` (position_bot) — balance / holdings snapshots.
    """

    def __init__(
        self,
        trade: MessageSender | None = None,
        position: MessageSender | None = None,
    ) -> None:
        self.trade = trade
        self.position = position

    def started(self, *, dry_run: bool) -> None:
        if self.trade is None:
            return
        mode = "paper" if dry_run else "live"
        self.trade.send(f"🟢 <b>{STRATEGY}</b> started\nmode={mode} dry_run={dry_run}")

    def market_open(self, *, start: str, end: str, dry_run: bool) -> None:
        if self.trade is None:
            return
        now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
        self.trade.send(
            f"🔔 <b>{STRATEGY} · market open</b>{_dry_suffix(dry_run)}\n"
            f"🕐 {now_kst}\n"
            f"Session {html.escape(start)}–{html.escape(end)} KST"
        )

    def market_close(self, *, start: str, end: str, dry_run: bool) -> None:
        if self.trade is None:
            return
        now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
        self.trade.send(
            f"🌙 <b>{STRATEGY} · market closed</b>{_dry_suffix(dry_run)}\n"
            f"🕐 {now_kst}\n"
            f"Session {html.escape(start)}–{html.escape(end)} KST\n"
            f"Hourly balance updates stopped until next open."
        )

    def order_placed(
        self,
        *,
        symbol: str,
        side: str,
        quantity: int,
        price: Decimal,
        dry_run: bool,
        order_id: str | None = None,
        stop_price: Decimal | None = None,
        take_profit_price: Decimal | None = None,
        name: str | None = None,
    ) -> None:
        if self.trade is None:
            return
        label = "BUY 🔺" if side.upper() == "BUY" else "SELL 🔻"
        lines = [
            f"🟢 <b>Order placed{_dry_suffix(dry_run)}</b>",
            f"Strategy: <b>{STRATEGY}</b>",
            f"Symbol: <b>{_symbol_label(symbol, name)}</b>  {label}",
            f"Price: {_krw(price)}  Qty: {quantity}",
            f"Notional: {_krw(price * quantity)}",
        ]
        if take_profit_price is not None and stop_price is not None and side.upper() == "BUY":
            lines.append(f"TP: {_krw(take_profit_price)}   SL: {_krw(stop_price)}")
        if order_id:
            lines.append(f"Order: <code>{_esc(order_id)}</code>")
        self.trade.send("\n".join(lines))

    def order_filled(
        self,
        *,
        symbol: str,
        side: str,
        quantity: int,
        price: Decimal,
        dry_run: bool,
        reason: str | None = None,
        pnl: Decimal | None = None,
        entry_price: Decimal | None = None,
        name: str | None = None,
    ) -> None:
        if self.trade is None:
            return
        label = "BUY 🔺" if side.upper() == "BUY" else "SELL 🔻"
        if side.upper() == "BUY":
            header = f"🟢 <b>Filled{_dry_suffix(dry_run)}</b>"
        else:
            emoji = _pnl_emoji(pnl) if pnl is not None else "🟢"
            reason_bit = f"  [{_esc(reason)}]" if reason else ""
            header = f"{emoji} <b>Position closed{_dry_suffix(dry_run)}</b>{reason_bit}"
        lines = [
            header,
            f"Strategy: <b>{STRATEGY}</b>",
            f"Symbol: <b>{_symbol_label(symbol, name)}</b>  {label}",
        ]
        if side.upper() == "SELL" and entry_price is not None:
            lines.append(f"Entry: {_krw(entry_price)}   Exit: {_krw(price)}")
        else:
            lines.append(f"Price: {_krw(price)}  Qty: {quantity}")
        if pnl is not None:
            lines.append(f"PnL: {_sgn(pnl)} KRW")
        self.trade.send("\n".join(lines))

    def scan_complete(
        self,
        payload: dict[str, Any],
        *,
        source: str = "scan",
    ) -> None:
        if self.trade is None:
            return
        accepted = list(payload.get("accepted") or [])
        rejected = payload.get("rejected") or {}
        universe = list(payload.get("universe") or [])
        names = {str(row.get("symbol")): str(row.get("name") or "") for row in universe}
        now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
        lines = [
            f"📋 <b>{STRATEGY} · {_esc(source)}</b>",
            f"🕐 {now_kst}",
            f"Accepted <b>{len(accepted)}</b>  /  watchlist {len(universe)}  /  rejected {len(rejected)}",
            "",
        ]
        if not accepted:
            lines.append("No names passed the buy filters. Next session will not place new entries.")
            self.trade.send("\n".join(lines))
            return
        for row in accepted:
            symbol = str(row.get("symbol") or "")
            name = names.get(symbol) or ""
            market = str(row.get("market") or "")
            close = _krw(row.get("close") or 0)
            label = _esc(symbol)
            if name:
                label = f"{label} {_esc(name)}"
            extra = f"  {_esc(market)}" if market else ""
            reasons = [str(x) for x in (row.get("reasons") or []) if x]
            tag = f"  · {_esc(', '.join(reasons))}" if reasons else ""
            lines.append(f"• <b>{label}</b>{extra}{tag}\n  Close {close}")
        self.trade.send("\n".join(lines))

    def kill_switch(self, reason: str) -> None:
        if self.trade is None:
            return
        self.trade.send(
            f"🛑 <b>{STRATEGY}</b> kill switch tripped\n{_esc(reason)}\nNo new entries until reset."
        )

    def crashed(self, error: BaseException) -> None:
        text = (
            f"🔴 <b>{STRATEGY} CRASHED</b>\n"
            f"<code>{_esc(type(error).__name__)}: {error}</code>\n"
            f"Check logs. Open positions may still be live."
        )
        if self.trade is not None:
            self.trade.send(text)
        if self.position is not None and self.position is not self.trade:
            self.position.send(text)

    def balance_update(
        self,
        *,
        cash: Decimal,
        nav: Decimal,
        positions: list[dict[str, Any]],
        realized_today: Decimal,
        marks: dict[str, Decimal] | None = None,
        names: dict[str, str] | None = None,
        settlement: dict[str, Any] | None = None,
        kind: str = "hourly",
    ) -> None:
        if self.position is None:
            return
        titles = {
            "open": "session open",
            "hourly": "hourly update",
            "close": "session close",
            "manual": "snapshot",
        }
        title = titles.get(kind, kind)
        now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
        marks = marks or {}
        names = names or {}
        settle_lines: list[str] = []
        if settlement:
            ladder = settlement.get("settlement") or {}
            for key in ("T", "T+1", "T+2"):
                row = ladder.get(key) or {}
                day = row.get("date") or ""
                settle_cash = _krw(row.get("cash") or 0)
                inflow = to_decimal(row.get("inflow") or 0, default=Decimal("0"))
                extra = f"  ({_sgn(inflow)} KRW settle)" if inflow else ""
                settle_lines.append(f"{key} {day}: <b>{settle_cash}</b>{extra}")
        balance_line = f"💰 Balance: <b>{_krw(nav)}</b>  Avail: {_krw(cash)}"
        settle_block = ("\n".join(settle_lines) + "\n") if settle_lines else ""
        header = f"📊 <b>{STRATEGY} · {title}</b>"
        if not positions:
            self.position.send(
                f"{header}\n🕐 {now_kst}\n{balance_line}\n"
                f"{settle_block}"
                f"Realized today: {_sgn(realized_today)} KRW\nNo open positions."
            )
            return
        lines = [
            header,
            f"🕐 {now_kst}",
            balance_line,
            *settle_lines,
            f"Realized today: {_sgn(realized_today)} KRW",
            "",
        ]
        total_upnl = Decimal("0")
        for pos in positions:
            symbol = str(pos["symbol"])
            qty = int(pos["quantity"])
            entry = to_decimal(pos["avg_price"])
            mark = marks.get(symbol, entry)
            upnl = (mark - entry) * qty
            total_upnl += upnl
            cost = entry * qty
            pct = (upnl / cost * 100) if cost else Decimal("0")
            label = _symbol_label(symbol, names.get(symbol) or str(pos.get("name") or ""))
            lines.append(
                f"{_pnl_emoji(upnl)} <b>{label}</b>  Long 🔺\n"
                f"  Entry {_krw(entry)}  Mark {_krw(mark)}  Size {qty}\n"
                f"  PnL {_sgn(upnl)} KRW  ({_sgn(pct, 2)}%)"
            )
        lines.append(f"\n{_pnl_emoji(total_upnl)} Total open PnL: <b>{_sgn(total_upnl)} KRW</b>")
        self.position.send("\n".join(lines))
