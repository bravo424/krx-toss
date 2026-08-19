from __future__ import annotations

from datetime import date
from decimal import Decimal

from krx_toss.jobs.settlement import SettlementFlow, net_cash_from_order, project_ladder


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


def test_buy_does_not_add_to_future_ladder():
    t = date(2026, 8, 19)
    t1 = date(2026, 8, 20)
    t2 = date(2026, 8, 21)
    flows = [
        SettlementFlow(t2, Decimal("500000"), "005930", "SELL"),
        SettlementFlow(t2, Decimal("-200000"), "005930", "BUY"),
    ]
    ladder = project_ladder(Decimal("1000000"), flows, t, t1, t2)
    assert ladder["T"]["cash"] == Decimal("1000000")
    assert ladder["T+1"]["cash"] == Decimal("1000000")
    assert ladder["T+1"]["inflow"] == Decimal("0")
    assert ladder["T+2"]["inflow"] == Decimal("500000")
    assert ladder["T+2"]["cash"] == Decimal("1500000")
