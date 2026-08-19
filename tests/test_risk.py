from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from krx_toss.strategy.risk import RiskLimits, daily_loss_breached, entries_allowed, parse_hhmm, size_buy


def limits() -> RiskLimits:
    return RiskLimits.from_strategy(
        {
            "risk": {
                "max_positions": 8,
                "position_nav_pct": "0.10",
                "cash_buffer_pct": "0.20",
                "per_name_risk_pct": "0.02",
                "daily_loss_kill_pct": "0.02",
                "max_notional_per_name": "5000000000",
                "high_value_threshold": "100000000",
            },
            "exit": {"stop_loss": "0.04"},
        }
    )


def test_parse_hhmm_tolerates_api_shapes():
    assert parse_hhmm("09:15") == time(9, 15)
    assert parse_hhmm("09") == time(9, 0)
    assert parse_hhmm("0900") == time(9, 0)
    assert parse_hhmm("09:00:00") == time(9, 0)
    assert parse_hhmm("2026-08-19T15:30:00+09:00") == time(15, 30)
    assert parse_hhmm("not-a-time", default="15:30") == time(15, 30)


def test_entries_blocked_before_0915():
    kst = ZoneInfo("Asia/Seoul")
    assert not entries_allowed(datetime(2026, 8, 14, 9, 10, tzinfo=kst))
    assert entries_allowed(datetime(2026, 8, 14, 9, 15, tzinfo=kst))
    # 08:54 in China/HK is already 09:54 KST
    cst = ZoneInfo("Asia/Shanghai")
    assert entries_allowed(datetime(2026, 8, 19, 8, 54, tzinfo=cst))


def test_size_respects_risk_and_budget():
    intent = size_buy(
        nav=Decimal("100000000"),
        price=Decimal("70000"),
        market="KOSPI",
        open_positions=0,
        shares_outstanding=Decimal("5000000000"),
        limits=limits(),
    )
    assert intent is not None
    assert intent.quantity > 0
    assert intent.notional <= Decimal("10000000") + Decimal("70000")
    assert not intent.confirm_high_value


def test_no_size_when_book_full():
    assert (
        size_buy(
            nav=Decimal("100000000"),
            price=Decimal("70000"),
            market="KOSPI",
            open_positions=8,
            shares_outstanding=None,
            limits=limits(),
        )
        is None
    )


def test_daily_loss_kill():
    lim = limits()
    assert daily_loss_breached(Decimal("100000000"), Decimal("-2500000"), lim)
    assert not daily_loss_breached(Decimal("100000000"), Decimal("-100000"), lim)
