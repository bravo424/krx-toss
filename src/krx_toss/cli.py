from __future__ import annotations

import argparse
import json
import logging
import sys
from decimal import Decimal
from pathlib import Path

from krx_toss.backtest.cache import MarketCache
from krx_toss.backtest.engine import SymbolHistory, run_backtest
from krx_toss.config import Settings, load_settings
from krx_toss.cost.model import CostModel
from krx_toss.execution.blotter import Blotter
from krx_toss.execution.broker import Broker
from krx_toss.execution.kill_switch import KillSwitch
from krx_toss.factory import build_alerts, build_client
from krx_toss.jobs.close_scan import scan_signals
from krx_toss.jobs.eod import run_eod
from krx_toss.jobs.open_entry import place_entries
from krx_toss.jobs.order_sync import sync_fills
from krx_toss.jobs.overlay_job import run_overlay
from krx_toss.jobs.scheduler import run_scheduler
from krx_toss.jobs.settlement import settlement_snapshot
from krx_toss.jobs.telegram_job import push_balance_update
from krx_toss.logging_setup import setup_logging
from krx_toss.strategy.risk import RiskLimits
from krx_toss.toss.client import TossClient
from krx_toss.toss.decimal_utils import to_decimal

log = logging.getLogger(__name__)

LIVE_FLAG = "--i-understand-the-risk"


def _session(settings: Settings, *, dry_run: bool | None = None) -> tuple[TossClient, Broker]:
    client = build_client(settings)
    try:
        client.resolve_account()
    except Exception as exc:  # noqa: BLE001
        if not (dry_run if dry_run is not None else settings.dry_run):
            raise
        log.warning("account resolve skipped in dry-run: %s", exc)
    live_commission = None
    try:
        live_commission = CostModel.from_commissions_payload(client.get_commissions(), settings.strategy).commission_rate
    except Exception as exc:  # noqa: BLE001
        log.warning("using fallback commission: %s", exc)
    cost = CostModel.from_strategy(settings.strategy, live_commission=live_commission)
    blotter = Blotter(settings.blotter_db)
    kill = KillSwitch(settings.kill_switch)
    broker = Broker(
        client,
        blotter,
        kill,
        dry_run=settings.dry_run if dry_run is None else dry_run,
        cost=cost,
        limits=RiskLimits.from_strategy(settings.strategy),
        alerts=build_alerts(settings),
    )
    return client, broker


def cmd_scan(settings: Settings) -> int:
    client, broker = _session(settings, dry_run=True)
    cache = MarketCache(settings.cache_dir)
    payload = scan_signals(client, settings, cache, alerts=broker.alerts, source="scan")
    print(json.dumps({"accepted": len(payload["accepted"]), "rejected": len(payload["rejected"])}, indent=2))
    return 0


def cmd_paper(settings: Settings) -> int:
    client, broker = _session(settings, dry_run=True)
    cache = MarketCache(settings.cache_dir)
    if not settings.signals_path.exists():
        scan_signals(client, settings, cache, alerts=broker.alerts, source="paper")
    placed = place_entries(client, broker, settings)
    print(json.dumps({"dry_run": True, "orders": len(placed)}, indent=2))
    return 0


def cmd_live(settings: Settings, confirmed: bool) -> int:
    if not confirmed:
        print(
            f"Refusing live trading. Re-run with {LIVE_FLAG} after setting dry_run: false in config/settings.yaml.",
            file=sys.stderr,
        )
        return 2
    if settings.dry_run:
        print("config/settings.yaml still has dry_run: true. Flip it to false for live orders.", file=sys.stderr)
        return 2
    client, broker = _session(settings, dry_run=False)
    placed = place_entries(client, broker, settings)
    fills = sync_fills(broker)
    print(json.dumps({"dry_run": False, "orders": len(placed), "sync": fills}, indent=2, default=str))
    return 0


def cmd_overlay(settings: Settings) -> int:
    client, broker = _session(settings)
    actions = run_overlay(client, broker, settings)
    print(json.dumps({"actions": actions}, indent=2))
    return 0


def cmd_eod(settings: Settings) -> int:
    client, broker = _session(settings)
    run_eod(client, broker, settings)
    return 0


def cmd_run(settings: Settings, once: bool) -> int:
    client, broker = _session(settings)
    try:
        run_scheduler(client, broker, settings, once=once)
    except KeyboardInterrupt:
        log.info("scheduler stopped")
        return 0
    except Exception as exc:
        try:
            broker.alerts.crashed(exc)
        except Exception as alert_exc:  # noqa: BLE001
            log.warning("crash telegram failed: %s", alert_exc)
        raise
    return 0


def cmd_alert(settings: Settings) -> int:
    client, broker = _session(settings)
    del client
    push_balance_update(broker, settings)
    print("telegram balance update sent")
    return 0


def cmd_balance(settings: Settings) -> int:
    client, broker = _session(settings, dry_run=False)
    snap = settlement_snapshot(client, broker)
    print(json.dumps(snap, indent=2, default=str))
    return 0


def cmd_backtest(settings: Settings, nav: Decimal) -> int:
    cache = MarketCache(settings.cache_dir)
    signals = cache.read_json("signals.json") or {}
    symbols = [row["symbol"] for row in (signals.get("universe") or [])]
    if not symbols:
        print("No cached universe. Run `krx-toss scan` or `krx-toss fetch-cache` first.", file=sys.stderr)
        return 1
    histories = {}
    for row in signals.get("universe") or []:
        symbol = row["symbol"]
        candles = cache.candles(symbol)
        if len(candles) < 30:
            continue
        histories[symbol] = SymbolHistory(
            market=row.get("market") or "KOSPI",
            candles=candles,
            flow=cache.flow(symbol),
            credit=cache.credit(symbol),
        )
    kospi = cache.candles("KOSPI")
    cost = CostModel.from_strategy(settings.strategy)
    limits = RiskLimits.from_strategy(settings.strategy)
    exit_cfg = settings.strategy_section("exit")
    result = run_backtest(
        histories,
        kospi=kospi,
        cost=cost,
        limits=limits,
        signal_params=settings.strategy_section("signal"),
        start_nav=nav,
        slippage_ticks=int((settings.strategy.get("cost") or {}).get("slippage_ticks", 1)),
        take_profit=to_decimal(exit_cfg.get("take_profit", "0.06")),
        stop_loss=to_decimal(exit_cfg.get("stop_loss", "0.04")),
        time_stop=int(exit_cfg.get("time_stop_sessions", 5)),
    )
    print(
        json.dumps(
            {
                "trades": len(result.trades),
                "win_rate": str(result.win_rate),
                "total_return": str(result.total_return),
                "start_nav": str(result.start_nav),
                "end_nav": str(result.end_nav),
            },
            indent=2,
        )
    )
    return 0


def cmd_fetch_cache(settings: Settings) -> int:
    client, broker = _session(settings, dry_run=True)
    cache = MarketCache(settings.cache_dir)
    scan_signals(client, settings, cache, alerts=broker.alerts, source="fetch-cache", persist=True)
    try:
        kospi = client.get_indicator_candles("KOSPI", interval="1d", count=200)
        cache.save_records("candles", "KOSPI", kospi.get("candles") or [])
    except Exception as exc:  # noqa: BLE001
        log.warning("KOSPI cache failed: %s", exc)
    print(f"cache written to {settings.cache_dir}")
    return 0


def cmd_status(settings: Settings) -> int:
    kill = KillSwitch(settings.kill_switch)
    blotter = Blotter(settings.blotter_db)
    print(
        json.dumps(
            {
                "dry_run": settings.dry_run,
                "kill_switch": kill.status(),
                "positions": blotter.positions(),
            },
            indent=2,
            default=str,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="krx-toss", description="KRX MFT/LFT platform on Toss Open API")
    parser.add_argument("--root", type=Path, default=None, help="Project root (default: package parent)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan", help="After-close universe + signal scan")
    sub.add_parser("paper", help="Place dry-run LIMIT entries from last scan")
    live = sub.add_parser("live", help="Place real LIMIT entries (requires confirmation)")
    live.add_argument(LIVE_FLAG, dest="confirmed", action="store_true")
    sub.add_parser("overlay", help="Holdings-only 1m overlay")
    sub.add_parser("eod", help="Time-stop flatten and session bump")
    run = sub.add_parser("run", help="Calendar-driven loop")
    run.add_argument("--once", action="store_true")
    bt = sub.add_parser("backtest", help="Replay cached daily bars with tax and fees")
    bt.add_argument("--nav", default="100000000")
    sub.add_parser("fetch-cache", help="Pull rankings/candles/flow into parquet")
    sub.add_parser("status", help="Show kill switch and blotter")
    sub.add_parser("balance", help="Show KRW cash on T / T+1 / T+2 settlement dates")
    sub.add_parser("alert", help="Send a Telegram balance / holdings snapshot now")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings(args.root)
    setup_logging(settings)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    if args.cmd == "scan":
        return cmd_scan(settings)
    if args.cmd == "paper":
        return cmd_paper(settings)
    if args.cmd == "live":
        return cmd_live(settings, args.confirmed)
    if args.cmd == "overlay":
        return cmd_overlay(settings)
    if args.cmd == "eod":
        return cmd_eod(settings)
    if args.cmd == "run":
        return cmd_run(settings, args.once)
    if args.cmd == "backtest":
        return cmd_backtest(settings, to_decimal(args.nav))
    if args.cmd == "fetch-cache":
        return cmd_fetch_cache(settings)
    if args.cmd == "status":
        return cmd_status(settings)
    if args.cmd == "balance":
        return cmd_balance(settings)
    if args.cmd == "alert":
        return cmd_alert(settings)
    parser.error(f"unknown command {args.cmd}")
    return 2
