from __future__ import annotations

import logging
from datetime import date, time
from decimal import Decimal

from krx_toss.config import Settings
from krx_toss.execution.broker import Broker
from krx_toss.jobs.settlement import settlement_snapshot
from krx_toss.strategy.risk import parse_hhmm
from krx_toss.strategy.universe import stock_display_name
from krx_toss.toss.decimal_utils import to_decimal

log = logging.getLogger(__name__)

# Periodic snapshots stop at 15:00 KST; session-end still fires at regular close (15:30).
HOURLY_STOP = "15:00"


def next_balance_kind(
    *,
    open_today: bool,
    clock: time,
    session_start: str,
    session_end: str,
    open_sent: bool,
    close_sent: bool,
    hourly_due: bool,
) -> str | None:
    """Which balance Telegram to send this tick, if any."""
    if not open_today:
        return None
    start = parse_hhmm(session_start, default="09:00")
    end = parse_hhmm(session_end, default="15:30")
    hourly_until = min(parse_hhmm(HOURLY_STOP, default="15:00"), end)
    if clock >= end and not close_sent:
        return "close"
    if start <= clock < end and not open_sent:
        return "open"
    if start <= clock < hourly_until and open_sent and hourly_due:
        return "hourly"
    return None


def fetch_marks(broker: Broker, symbols: list[str]) -> dict[str, Decimal]:
    if not symbols or broker.dry_run:
        return {}
    return broker.last_prices(symbols)


def fetch_names(broker: Broker, symbols: list[str]) -> dict[str, str]:
    if not symbols:
        return {}
    names: dict[str, str] = {}
    try:
        for row in broker.client.get_stocks(symbols) or []:
            symbol = str(row.get("symbol") or "")
            name = stock_display_name(row)
            if symbol and name:
                names[symbol] = name
    except Exception as exc:  # noqa: BLE001
        log.warning("stock names failed: %s", exc)
    if names:
        broker.remember_names(names)
    return names


def marked_equity(
    cash: Decimal,
    positions: list[dict],
    marks: dict[str, Decimal] | None = None,
) -> Decimal:
    """Avail cash plus qty × last price (falls back to avg cost if no mark)."""
    equity = cash
    marks = marks or {}
    for pos in positions:
        qty = int(pos["quantity"])
        cost = to_decimal(pos.get("avg_price") or 0, default=Decimal("0"))
        mark = marks.get(str(pos["symbol"]), cost)
        if mark <= 0:
            mark = cost
        equity += mark * qty
    return equity


def push_balance_update(
    broker: Broker,
    settings: Settings | None = None,
    *,
    kind: str = "manual",
) -> None:
    positions = broker.blotter.positions()
    cash = broker.buying_power_krw()
    symbols = [str(p["symbol"]) for p in positions]
    marks = fetch_marks(broker, symbols)
    names = fetch_names(broker, symbols)
    nav = marked_equity(cash, positions, marks)
    settle = None
    try:
        settle = settlement_snapshot(broker.client, broker)
    except Exception as exc:  # noqa: BLE001
        log.warning("settlement snapshot failed: %s", exc)
    broker.alerts.balance_update(
        cash=cash,
        nav=nav,
        positions=positions,
        realized_today=broker.blotter.realized_on(date.today()),
        marks=marks,
        names=names,
        settlement=settle,
        kind=kind,
    )
