from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from krx_toss.alerts import TradingAlerts
from krx_toss.backtest.cache import MarketCache
from krx_toss.config import Settings
from krx_toss.strategy.features import parse_candles, parse_credit, parse_flow
from krx_toss.strategy.signals import Signal, index_blocks_entries, select_signals
from krx_toss.strategy.universe import (
    UniverseName,
    blocked_warning_set,
    build_universe,
    is_tradable_stock,
    merge_ranking_symbols,
)
from krx_toss.toss.client import TossClient
from krx_toss.toss.decimal_utils import to_decimal

log = logging.getLogger(__name__)


def _ranking_types(uni: dict[str, Any]) -> list[str]:
    raw = uni.get("ranking_types")
    if isinstance(raw, list) and raw:
        return [str(item) for item in raw]
    return [str(uni.get("ranking_type", "MARKET_TRADING_AMOUNT"))]


def _universe_rows(names: list[UniverseName]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in names:
        row: dict[str, Any] = {"symbol": name.symbol, "market": name.market, "name": name.name}
        if name.shares_outstanding is not None:
            row["shares_outstanding"] = str(name.shares_outstanding)
        rows.append(row)
    return rows


def _write_signals(cache: MarketCache, settings: Settings, payload: dict[str, Any]) -> None:
    path = cache.write_json("signals.json", payload)
    dest = settings.signals_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.resolve() != path.resolve():
        dest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")


def fetch_universe(client: TossClient, settings: Settings) -> list[UniverseName]:
    uni = settings.strategy_section("universe")
    per_list = min(int(uni.get("ranking_count", 100)), 100)
    watchlist_size = int(uni.get("watchlist_size", 60))
    markets = uni.get("markets") or ["KOSPI", "KOSDAQ"]
    common_only = bool(uni.get("common_share_only", True))
    blocked = blocked_warning_set(uni.get("blocked_warning_types"))
    payloads = []
    for ranking_type in _ranking_types(uni):
        for duration in uni.get("ranking_durations") or ["1d"]:
            payloads.append(
                client.get_rankings(
                    ranking_type=ranking_type,
                    market_country="KR",
                    duration=str(duration),
                    exclude_investment_caution=bool(uni.get("exclude_investment_caution", True)),
                    count=per_list,
                )
            )
    # Toss caps each ranking at 100. Keep the unique merge — do not clip back to 100.
    ranked = merge_ranking_symbols(*payloads, limit=max(watchlist_size * 2, per_list), per_list=per_list)
    symbols = [s for s, _ in ranked]
    info_rows = client.get_stocks(symbols) if symbols else []
    info = {str(row.get("symbol")): row for row in info_rows}
    tradable = [
        (symbol, score)
        for symbol, score in ranked
        if is_tradable_stock(info.get(symbol) or {}, markets=markets, common_only=common_only)
    ]
    warnings: dict[str, list[dict[str, Any]]] = {}
    # Warnings are STOCK group at 5 TPS; only ask for tradable names until the watchlist can fill.
    warning_limit = min(len(tradable), max(watchlist_size + 20, int(watchlist_size * 1.15)))
    for symbol, _score in tradable[:warning_limit]:
        try:
            warnings[symbol] = client.get_warnings(symbol)
        except Exception as exc:  # noqa: BLE001
            log.warning("warnings failed for %s: %s", symbol, exc)
            warnings[symbol] = []
    universe = build_universe(
        rankings=tradable,
        stock_info=info,
        warnings=warnings,
        markets=markets,
        common_only=common_only,
        blocked_warnings=blocked,
        watchlist_size=watchlist_size,
    )
    log.info("universe ranked=%s tradable=%s watchlist=%s target=%s", len(ranked), len(tradable), len(universe.names), watchlist_size)
    return universe.names


def scan_signals(
    client: TossClient,
    settings: Settings,
    cache: MarketCache,
    *,
    alerts: TradingAlerts | None = None,
    source: str = "scan",
    persist: bool = False,
) -> dict[str, Any]:
    params = settings.strategy_section("signal")
    kospi = parse_candles(client.get_indicator_candles("KOSPI", interval="1d", count=30))
    skip = Decimal(str(params.get("kospi_skip_1d_return", "-0.02")))
    if index_blocks_entries(kospi, skip):
        payload = {"accepted": [], "rejected": {"KOSPI": "kospi_risk_off"}, "universe": []}
        _write_signals(cache, settings, payload)
        log.info("scan complete kospi_risk_off skipped watchlist fetch")
        if alerts is not None:
            try:
                alerts.scan_complete(payload, source=source)
            except Exception as exc:  # noqa: BLE001
                log.warning("scan telegram failed: %s", exc)
        return payload

    names = fetch_universe(client, settings)
    candidates = []
    for name in names:
        fetched = cache.fetch_symbol(client, name.symbol, bars=80, persist=persist)
        candidates.append(
            (
                name.symbol,
                name.market,
                parse_candles(fetched["candles"]),
                parse_flow(fetched["flow"]),
                parse_credit(fetched["credit"]),
            )
        )
    decision = select_signals(candidates, params, kospi_candles=kospi)
    payload = {
        "accepted": [
            {
                "symbol": s.symbol,
                "market": s.market,
                "close": str(s.close),
                "ma20": str(s.ma20),
                "ret_20d": str(s.ret_20d),
                "ret_3d": str(s.ret_3d),
                "foreign_net": str(s.foreign_net),
                "institution_net": str(s.institution_net),
                "reasons": s.reasons,
            }
            for s in decision.accepted
        ],
        "rejected": decision.rejected,
        "universe": _universe_rows(names),
    }
    _write_signals(cache, settings, payload)
    log.info("scan complete accepted=%s rejected=%s", len(decision.accepted), len(decision.rejected))
    if alerts is not None:
        try:
            alerts.scan_complete(payload, source=source)
        except Exception as exc:  # noqa: BLE001
            log.warning("scan telegram failed: %s", exc)
    return payload


def signals_from_payload(payload: dict[str, Any]) -> list[Signal]:
    out: list[Signal] = []
    for row in payload.get("accepted") or []:
        out.append(
            Signal(
                symbol=str(row["symbol"]),
                market=str(row.get("market") or "KOSPI"),
                close=to_decimal(row["close"]),
                ma20=to_decimal(row.get("ma20") or row["close"]),
                ret_20d=to_decimal(row.get("ret_20d") or 0),
                ret_3d=to_decimal(row.get("ret_3d") or 0),
                foreign_net=to_decimal(row.get("foreign_net") or 0),
                institution_net=to_decimal(row.get("institution_net") or 0),
                reasons=list(row.get("reasons") or []),
            )
        )
    return out
