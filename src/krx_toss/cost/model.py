from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Mapping

from krx_toss.toss.decimal_utils import to_decimal

# 2026 listed-share STT (sell only). KOSPI = 0.05% STT + 0.15% rural surcharge.
DEFAULT_STT = {
    "KOSPI": Decimal("0.0020"),
    "KOSDAQ": Decimal("0.0020"),
    "KONEX": Decimal("0.0010"),
    "KR_ETC": Decimal("0.0020"),
}

DEFAULT_COMMISSION = Decimal("0.00015")


@dataclass(frozen=True)
class CostModel:
    commission_rate: Decimal
    stt_by_market: Mapping[str, Decimal]

    @classmethod
    def from_strategy(cls, strategy: Mapping[str, Any], live_commission: Decimal | None = None) -> CostModel:
        cost = strategy.get("cost") or {}
        fallback = to_decimal(cost.get("fallback_commission_rate", DEFAULT_COMMISSION))
        stt_raw = cost.get("stt") or {}
        stt = {str(k).upper(): to_decimal(v) for k, v in stt_raw.items()} if stt_raw else dict(DEFAULT_STT)
        return cls(commission_rate=live_commission if live_commission is not None else fallback, stt_by_market=stt)

    @classmethod
    def from_commissions_payload(
        cls,
        payload: list[dict[str, Any]] | dict[str, Any],
        strategy: Mapping[str, Any],
        as_of: date | None = None,
    ) -> CostModel:
        cost = strategy.get("cost") or {}
        fallback = to_decimal(cost.get("fallback_commission_rate", DEFAULT_COMMISSION))
        rate = pick_kr_commission(payload, as_of=as_of, fallback=fallback)
        return cls.from_strategy(strategy, live_commission=rate)

    def commission(self, notional: Decimal) -> Decimal:
        return (to_decimal(notional) * self.commission_rate).quantize(Decimal("0.0001"))

    def stt_rate(self, market: str) -> Decimal:
        return to_decimal(self.stt_by_market.get(str(market).upper(), DEFAULT_STT["KOSPI"]))

    def sell_tax(self, notional: Decimal, market: str) -> Decimal:
        return (to_decimal(notional) * self.stt_rate(market)).quantize(Decimal("0.0001"))

    def buy_cost(self, notional: Decimal) -> Decimal:
        return self.commission(notional)

    def sell_cost(self, notional: Decimal, market: str) -> Decimal:
        return self.commission(notional) + self.sell_tax(notional, market)

    def round_trip_cost(self, buy_notional: Decimal, sell_notional: Decimal, market: str) -> Decimal:
        return self.buy_cost(buy_notional) + self.sell_cost(sell_notional, market)

    def round_trip_rate(self, market: str) -> Decimal:
        return self.commission_rate * 2 + self.stt_rate(market)

    def net_pnl(self, buy_notional: Decimal, sell_notional: Decimal, market: str) -> Decimal:
        return to_decimal(sell_notional) - to_decimal(buy_notional) - self.round_trip_cost(buy_notional, sell_notional, market)


def pick_kr_commission(
    payload: list[dict[str, Any]] | dict[str, Any],
    *,
    as_of: date | None = None,
    fallback: Decimal = DEFAULT_COMMISSION,
) -> Decimal:
    rows = payload if isinstance(payload, list) else payload.get("commissions") or payload.get("items") or [payload]
    today = as_of or date.today()
    candidates: list[Decimal] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        country = str(row.get("marketCountry") or row.get("market") or "").upper()
        if country and country not in {"KR", "KRX", "KOSPI", "KOSDAQ"}:
            continue
        start = _parse_date(row.get("startDate"))
        end = _parse_date(row.get("endDate"))
        if start and today < start:
            continue
        if end and today > end:
            continue
        rate = row.get("commissionRate")
        if rate is not None:
            candidates.append(to_decimal(rate))
    return min(candidates) if candidates else fallback


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    text = str(value)[:10]
    try:
        y, m, d = text.split("-")
        return date(int(y), int(m), int(d))
    except ValueError:
        return None
