from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

HARD_EXCLUDE_WARNINGS = {
    "LIQUIDATION_TRADING",
    "OVERHEATED",
    "INVESTMENT_WARNING",
    "INVESTMENT_RISK",
    "STOCK_WARRANTS",
}


@dataclass
class UniverseName:
    symbol: str
    name: str = ""
    market: str = "KOSPI"
    shares_outstanding: Decimal | None = None
    ranking_score: Decimal = Decimal("0")


@dataclass
class Universe:
    names: list[UniverseName] = field(default_factory=list)

    @property
    def symbols(self) -> list[str]:
        return [n.symbol for n in self.names]


def ranking_symbols(payload: dict[str, Any], limit: int) -> list[tuple[str, Decimal]]:
    rows = payload.get("rankings") or payload.get("items") or []
    out: list[tuple[str, Decimal]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "")
        if not symbol:
            continue
        score = row.get("tradingAmount") or row.get("value") or row.get("tradingVolume") or 0
        try:
            amount = Decimal(str(score))
        except Exception:
            amount = Decimal("0")
        out.append((symbol, amount))
        if len(out) >= limit:
            break
    return out


def merge_ranking_symbols(
    *payloads: dict[str, Any],
    limit: int,
    per_list: int = 100,
) -> list[tuple[str, Decimal]]:
    scores: dict[str, Decimal] = {}
    for payload in payloads:
        for symbol, amount in ranking_symbols(payload, limit=per_list):
            scores[symbol] = max(scores.get(symbol, Decimal("0")), amount)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return ranked[:limit] if limit > 0 else ranked


def stock_display_name(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    return str(row.get("name") or row.get("koreanName") or row.get("stockName") or "")


def blocked_warning_set(configured: Iterable[str] | None) -> set[str]:
    return HARD_EXCLUDE_WARNINGS | {str(x).upper() for x in (configured or [])}


def is_tradable_stock(info: dict[str, Any], *, markets: Iterable[str], common_only: bool) -> bool:
    market = str(info.get("market") or "").upper()
    if market not in {m.upper() for m in markets}:
        return False
    if str(info.get("status") or "ACTIVE").upper() != "ACTIVE":
        return False
    if str(info.get("securityType") or "STOCK").upper() != "STOCK":
        return False
    if common_only and info.get("isCommonShare") is False:
        return False
    detail = info.get("koreanMarketDetail") or {}
    if detail.get("liquidationTrading") or detail.get("krxTradingSuspended"):
        return False
    return True


def warning_blocked(warnings: list[dict[str, Any]], blocked: Iterable[str]) -> bool:
    blocked_set = {str(x).upper() for x in blocked}
    for item in warnings:
        wtype = str(item.get("warningType") or "").upper()
        if wtype in blocked_set:
            return True
    return False


def vi_active(warnings: list[dict[str, Any]]) -> bool:
    return any(str(item.get("warningType") or "").startswith("VI_") for item in warnings)


def build_universe(
    *,
    rankings: list[tuple[str, Decimal]],
    stock_info: dict[str, dict[str, Any]],
    warnings: dict[str, list[dict[str, Any]]],
    markets: Iterable[str],
    common_only: bool,
    blocked_warnings: Iterable[str],
    watchlist_size: int,
) -> Universe:
    names: list[UniverseName] = []
    for symbol, score in rankings:
        info = stock_info.get(symbol) or {}
        if not is_tradable_stock(info, markets=markets, common_only=common_only):
            continue
        if warning_blocked(warnings.get(symbol) or [], blocked_warnings):
            continue
        shares = info.get("sharesOutstanding")
        names.append(
            UniverseName(
                symbol=symbol,
                name=stock_display_name(info),
                market=str(info.get("market") or "KOSPI"),
                shares_outstanding=Decimal(str(shares)) if shares not in (None, "") else None,
                ranking_score=score,
            )
        )
        if len(names) >= watchlist_size:
            break
    return Universe(names=names)
