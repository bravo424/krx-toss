from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from krx_toss.strategy.features import Candle, CreditDay, FlowDay


def make_candles(
    start: date,
    n: int,
    *,
    start_px: Decimal = Decimal("10000"),
    drift: Decimal = Decimal("50"),
) -> list[Candle]:
    out: list[Candle] = []
    px = start_px
    for i in range(n):
        d = start + timedelta(days=i)
        o = px
        c = px + drift
        h = max(o, c) + Decimal("20")
        low = min(o, c) - Decimal("20")
        out.append(
            Candle(
                timestamp=f"{d.isoformat()}T15:30:00+09:00",
                open=o,
                high=h,
                low=low,
                close=c,
                volume=Decimal("1000000"),
            )
        )
        px = c
    return out


def make_flow(start: date, n: int, foreign: int, institution: int) -> list[FlowDay]:
    return [
        FlowDay(
            date=(start + timedelta(days=i)).isoformat(),
            foreign_net=Decimal(foreign),
            institution_net=Decimal(institution),
        )
        for i in range(n)
    ]


def make_credit(start: date, n: int, balance: int = 1000) -> list[CreditDay]:
    return [
        CreditDay(date=(start + timedelta(days=i)).isoformat(), margin_balance=Decimal(balance))
        for i in range(n)
    ]
