from __future__ import annotations

from datetime import date
from decimal import Decimal

from krx_toss.jobs.settlement import (
    holdings_market_value_krw,
    net_cash_from_order,
    project_ladder,
    reserved_buy_notional,
)


def test_sell_net_cash_minus_fees():
    order = {
        "side": "SELL",
        "execution": {
            "filledQuantity": "10",
            "filledAmount": "710000",
            "commission": "107",
            "tax": "1420",
            "settlementDate": "2026-08-21",
        },
    }
    assert net_cash_from_order(order) == Decimal("708473")


def test_ladder_is_available_on_every_day():
    t = date(2026, 8, 25)
    t1 = date(2026, 8, 26)
    t2 = date(2026, 8, 27)
    ladder = project_ladder(Decimal("866437"), t, t1, t2)
    assert ladder["T"]["cash"] == Decimal("866437")
    assert ladder["T+1"]["cash"] == Decimal("866437")
    assert ladder["T+2"]["cash"] == Decimal("866437")
    assert ladder["T+1"]["inflow"] == Decimal("0")
    assert ladder["T+2"]["inflow"] == Decimal("0")


def test_holdings_market_value_reads_nested_krw():
    holdings = {
        "marketValue": {"amount": {"krw": "2184552", "usd": None}},
        "items": [
            {
                "symbol": "005930",
                "quantity": "10",
                "lastPrice": "71000",
                "marketValue": {"amount": "710000"},
            }
        ],
    }
    assert holdings_market_value_krw(holdings) == Decimal("2184552")


def test_reserved_buy_notional_uses_unfilled_qty():
    orders = [
        {"side": "BUY", "quantity": "10", "price": "70000", "execution": {"filledQuantity": "4"}},
        {"side": "SELL", "quantity": "3", "price": "80000", "execution": {"filledQuantity": "0"}},
    ]
    assert reserved_buy_notional(orders) == Decimal("420000")
