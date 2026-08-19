from __future__ import annotations

from datetime import date
from decimal import Decimal

from krx_toss.cost.model import CostModel, pick_kr_commission
from krx_toss.toss.decimal_utils import krx_tick_size, round_to_tick


def test_round_trip_dominated_by_stt():
    model = CostModel.from_strategy(
        {"cost": {"fallback_commission_rate": "0.00015", "stt": {"KOSPI": "0.0020", "KOSDAQ": "0.0020"}}}
    )
    buy = Decimal("10000000")
    sell = Decimal("10000000")
    rt = model.round_trip_cost(buy, sell, "KOSPI")
    assert model.sell_tax(sell, "KOSPI") == Decimal("20000.0000")
    assert rt > Decimal("20000")
    assert model.round_trip_rate("KOSPI") == Decimal("0.00230")


def test_pick_live_commission_prefers_kr_in_window():
    payload = [
        {"marketCountry": "US", "commissionRate": "0.001"},
        {"marketCountry": "KR", "commissionRate": "0.00015", "startDate": "2026-01-01", "endDate": None},
        {"marketCountry": "KR", "commissionRate": "0", "startDate": "2025-12-15", "endDate": "2026-06-30"},
    ]
    assert pick_kr_commission(payload, as_of=date(2026, 3, 1)) == Decimal("0")
    assert pick_kr_commission(payload, as_of=date(2026, 8, 14)) == Decimal("0.00015")


def test_tick_size_bands():
    assert krx_tick_size(Decimal("1500")) == Decimal("1")
    assert krx_tick_size(Decimal("12000")) == Decimal("10")
    assert krx_tick_size(Decimal("70000")) == Decimal("100")
    assert round_to_tick(Decimal("70150"), side="BUY") == Decimal("70100")
