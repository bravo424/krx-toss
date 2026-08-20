from __future__ import annotations

from datetime import date
from decimal import Decimal

from helpers import make_candles, make_credit, make_flow

from krx_toss.strategy.signals import evaluate_symbol, index_blocks_entries, select_signals
from krx_toss.strategy.universe import build_universe, merge_ranking_symbols, warning_blocked


PARAMS = {
    "flow_lookback_sessions": 3,
    "ma_window": 20,
    "min_20d_return": 0,
    "max_3d_return": "0.13",
    "credit_lookback": 20,
    "max_credit_vs_avg": "1.5",
    "kospi_skip_1d_return": "-0.02",
}


def test_accepts_flow_momentum():
    start = date(2026, 1, 1)
    candles = make_candles(start, 30, drift=Decimal("80"))
    flow = make_flow(start, 30, foreign=10000, institution=5000)
    credit = make_credit(start, 30, 1000)
    signal, reason = evaluate_symbol(
        symbol="005930", market="KOSPI", candles=candles, flow=flow, credit=credit, params=PARAMS
    )
    assert reason is None
    assert signal is not None
    assert signal.symbol == "005930"


def test_rejects_overextended():
    start = date(2026, 1, 1)
    candles = make_candles(start, 25, start_px=Decimal("10000"), drift=Decimal("10"))
    candles[-1].close = candles[-4].close * Decimal("1.20")
    candles[-1].high = candles[-1].close
    candles[-2].close = candles[-4].close * Decimal("1.10")
    candles[-3].close = candles[-4].close * Decimal("1.05")
    flow = make_flow(start, 25, 1, 1)
    credit = make_credit(start, 25)
    signal, reason = evaluate_symbol(
        symbol="000660", market="KOSPI", candles=candles, flow=flow, credit=credit, params=PARAMS
    )
    assert signal is None
    assert reason == "overextended_3d"


def test_parses_toss_open_price_fields():
    from krx_toss.strategy.features import parse_candles

    rows = [
        {
            "timestamp": f"2026-01-{i:02d}T00:00:00+09:00",
            "openPrice": "10000",
            "highPrice": "10100",
            "lowPrice": "9900",
            "closePrice": str(10000 + i * 50),
            "volume": "1",
        }
        for i in range(1, 25)
    ]
    candles = parse_candles({"candles": rows})
    assert len(candles) == 24
    signal, reason = evaluate_symbol(
        symbol="005930",
        market="KOSPI",
        candles=candles,
        flow=make_flow(date(2026, 1, 1), 24, 1000, 1000),
        credit=make_credit(date(2026, 1, 1), 24),
        params=PARAMS,
    )
    assert reason != "insufficient_candles"


def test_rejects_foreign_selling():
    start = date(2026, 1, 1)
    candles = make_candles(start, 30, drift=Decimal("40"))
    flow = make_flow(start, 30, foreign=-1000, institution=5000)
    credit = make_credit(start, 30)
    _, reason = evaluate_symbol(
        symbol="005930", market="KOSPI", candles=candles, flow=flow, credit=credit, params=PARAMS
    )
    assert reason == "foreign_not_buying"


def test_kospi_risk_off_blocks_all():
    start = date(2026, 1, 1)
    kospi = make_candles(start, 5, start_px=Decimal("3000"), drift=Decimal("-100"))
    assert index_blocks_entries(kospi, Decimal("-0.02")) is True
    decision = select_signals([], PARAMS, kospi_candles=kospi)
    assert decision.accepted == []


def test_universe_filters_warnings_and_etf():
    ranked = [("005930", Decimal("1")), ("069500", Decimal("1")), ("000001", Decimal("1"))]
    info = {
        "005930": {"symbol": "005930", "market": "KOSPI", "status": "ACTIVE", "securityType": "STOCK", "isCommonShare": True},
        "069500": {"symbol": "069500", "market": "KOSPI", "status": "ACTIVE", "securityType": "ETF", "isCommonShare": True},
        "000001": {"symbol": "000001", "market": "KOSPI", "status": "ACTIVE", "securityType": "STOCK", "isCommonShare": True},
    }
    warnings = {"000001": [{"warningType": "INVESTMENT_WARNING"}], "005930": []}
    uni = build_universe(
        rankings=ranked,
        stock_info=info,
        warnings=warnings,
        markets=["KOSPI"],
        common_only=True,
        blocked_warnings=["INVESTMENT_WARNING"],
        watchlist_size=10,
    )
    assert uni.symbols == ["005930"]
    assert warning_blocked(warnings["000001"], ["INVESTMENT_WARNING"])


def test_merge_keeps_unique_names_beyond_one_ranking_list():
    day = {"rankings": [{"symbol": f"{i:06d}", "tradingAmount": str(1000 - i)} for i in range(100)]}
    week = {"rankings": [{"symbol": f"{i:06d}", "tradingAmount": str(500 - i)} for i in range(80, 180)]}
    merged = merge_ranking_symbols(day, week, limit=200, per_list=100)
    assert len(merged) == 180
    assert merged[0][0] == "000000"
