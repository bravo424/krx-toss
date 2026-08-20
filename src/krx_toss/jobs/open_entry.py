from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from krx_toss.config import Settings
from krx_toss.execution.broker import Broker
from krx_toss.jobs.close_scan import signals_from_payload
from krx_toss.strategy.risk import RiskLimits, attach_symbol, entries_allowed, size_buy
from krx_toss.toss.client import TossClient
from krx_toss.toss.decimal_utils import apply_tick_offset, to_decimal

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


def estimate_nav(broker: Broker) -> Decimal:
    cash = broker.buying_power_krw()
    nav = cash
    for pos in broker.blotter.positions():
        nav += Decimal(pos["avg_price"]) * int(pos["quantity"])
    if broker.dry_run:
        return max(nav, Decimal("100000000"))
    return nav


def place_entries(client: TossClient, broker: Broker, settings: Settings, now: datetime | None = None) -> list[dict]:
    entry_cfg = settings.strategy_section("entry")
    exit_cfg = settings.strategy_section("exit")
    if not entries_allowed(now or datetime.now(KST), str(entry_cfg.get("no_new_orders_until", "09:15"))):
        log.info("entries blocked until %s KST", entry_cfg.get("no_new_orders_until"))
        return []
    if broker.kill_switch.tripped():
        log.warning("kill switch tripped; skip entries")
        return []
    if not settings.signals_path.exists():
        log.warning("no signals file at %s; run scan first", settings.signals_path)
        return []
    payload = json.loads(settings.signals_path.read_text(encoding="utf-8"))
    signals = signals_from_payload(payload)
    limits = RiskLimits.from_strategy(settings.strategy)
    nav = estimate_nav(broker)
    held = {str(p["symbol"]) for p in broker.blotter.positions()}
    pending_buys = {
        str(o["symbol"])
        for o in broker.blotter.pending_orders()
        if str(o.get("side") or "").upper() == "BUY"
    }
    blocked = held | pending_buys
    open_count = len(blocked)
    results = []
    offset = int(entry_cfg.get("limit_offset_ticks", 0))
    take_profit = to_decimal(exit_cfg.get("take_profit", "0.06"))
    universe_map = {row["symbol"]: row for row in payload.get("universe") or []}
    wanted = [s.symbol for s in signals if s.symbol not in blocked][: max(0, limits.max_positions - open_count)]
    marks = broker.last_prices(wanted)

    for signal in signals:
        if open_count >= limits.max_positions:
            break
        if signal.symbol in blocked:
            continue
        last = marks.get(signal.symbol) or signal.close
        px = apply_tick_offset(last, offset, signal.market, side="BUY")
        shares = None
        info = universe_map.get(signal.symbol) or {}
        if info.get("shares_outstanding"):
            shares = to_decimal(info["shares_outstanding"])
        raw = size_buy(
            nav=nav,
            price=px,
            market=signal.market,
            open_positions=open_count,
            shares_outstanding=shares,
            limits=limits,
        )
        if raw is None:
            continue
        intent = attach_symbol(raw, signal.symbol, take_profit)
        try:
            submitted = broker.submit_limit(intent)
        except Exception as exc:  # noqa: BLE001
            log.warning("entry submit failed %s: %s", signal.symbol, exc)
            continue
        # OCO is a SELL. Toss rejects it until the buy has actually filled
        # (retail cannot short). Attach now only in dry-run, where the fill is fake.
        if broker.dry_run:
            broker.ensure_oco(intent.symbol)
        blocked.add(signal.symbol)
        open_count = len(blocked)
        results.append(submitted)
        log.info("entry %s qty=%s px=%s", signal.symbol, intent.quantity, intent.price)
    return results
