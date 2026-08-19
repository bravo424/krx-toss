from __future__ import annotations

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from krx_toss.backtest.cache import MarketCache
from krx_toss.config import Settings
from krx_toss.execution.broker import Broker
from krx_toss.jobs.calendar import calendar_is_open, regular_session_times
from krx_toss.jobs.close_scan import scan_signals
from krx_toss.jobs.eod import run_eod
from krx_toss.jobs.open_entry import place_entries
from krx_toss.jobs.order_sync import sync_fills
from krx_toss.jobs.overlay_job import run_overlay
from krx_toss.jobs.telegram_job import next_balance_kind, push_balance_update
from krx_toss.strategy.risk import parse_hhmm
from krx_toss.toss.client import TossClient

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


def run_scheduler(client: TossClient, broker: Broker, settings: Settings, *, once: bool = False) -> None:
    cache = MarketCache(settings.cache_dir)
    entry_cfg = settings.strategy_section("entry")
    entry_at = parse_hhmm(str(entry_cfg.get("after_kst", "09:15")))
    last_scan_date = None
    last_entry_date = None
    last_eod_date = None
    last_open_balance_date = None
    last_close_balance_date = None
    last_order_sync = 0.0
    last_hourly_balance = 0.0
    last_overlay = 0.0
    try:
        broker.alerts.started(dry_run=broker.dry_run)
    except Exception as exc:  # noqa: BLE001
        log.warning("startup telegram failed: %s", exc)
    while True:
        try:
            now = datetime.now(KST)
            cal: dict = {}
            try:
                cal = client.get_kr_calendar()
                open_today = calendar_is_open(cal, now)
            except Exception as exc:  # noqa: BLE001
                log.warning("calendar fetch failed: %s", exc)
                open_today = now.weekday() < 5
            clock = now.time().replace(tzinfo=None)
            try:
                session_start, session_end = regular_session_times(cal)
            except Exception as exc:  # noqa: BLE001
                log.warning("session times failed: %s", exc)
                session_start, session_end = "09:00", "15:30"

            if time.time() - last_order_sync >= settings.order_status_seconds:
                try:
                    sync_fills(broker)
                except Exception as exc:  # noqa: BLE001
                    log.warning("fill sync failed: %s", exc)
                last_order_sync = time.time()
            kind = next_balance_kind(
                open_today=open_today,
                clock=clock,
                session_start=session_start,
                session_end=session_end,
                open_sent=last_open_balance_date == now.date(),
                close_sent=last_close_balance_date == now.date(),
                hourly_due=last_hourly_balance > 0
                and (time.time() - last_hourly_balance) >= settings.balance_update_seconds,
            )
            if kind:
                try:
                    if kind == "open":
                        broker.alerts.market_open(start=session_start, end=session_end, dry_run=broker.dry_run)
                    elif kind == "close":
                        broker.alerts.market_close(start=session_start, end=session_end, dry_run=broker.dry_run)
                    push_balance_update(broker, settings, kind=kind)
                except Exception as exc:  # noqa: BLE001
                    log.warning("session telegram failed: %s", exc)
                if kind == "open":
                    last_open_balance_date = now.date()
                    last_hourly_balance = time.time()
                elif kind == "hourly":
                    last_hourly_balance = time.time()
                elif kind == "close":
                    last_close_balance_date = now.date()

            if open_today and now.hour >= 15 and now.minute >= 45 and last_scan_date != now.date():
                try:
                    scan_signals(client, settings, cache, alerts=broker.alerts, source="run")
                    last_scan_date = now.date()
                except Exception as exc:  # noqa: BLE001
                    log.exception("scan failed: %s", exc)
            if open_today and clock >= entry_at and clock.hour < 15 and last_entry_date != now.date():
                try:
                    place_entries(client, broker, settings, now=now)
                    last_entry_date = now.date()
                except Exception as exc:  # noqa: BLE001
                    log.exception("entries failed: %s", exc)
            if open_today and entry_at <= clock and (now.hour < 15 or (now.hour == 15 and now.minute < 30)):
                if time.time() - last_overlay >= settings.overlay_seconds:
                    try:
                        run_overlay(client, broker, settings)
                    except Exception as exc:  # noqa: BLE001
                        log.exception("overlay failed: %s", exc)
                    last_overlay = time.time()
            if open_today and now.hour >= 15 and now.minute >= 35 and last_eod_date != now.date():
                try:
                    run_eod(client, broker, settings)
                    last_eod_date = now.date()
                except Exception as exc:  # noqa: BLE001
                    log.exception("eod failed: %s", exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("scheduler tick failed: %s", exc)

        if once:
            return
        time.sleep(max(1, min(settings.overlay_seconds, settings.order_status_seconds)))
