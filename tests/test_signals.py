from __future__ import annotations

from datetime import date
from decimal import Decimal

from helpers import make_candles, make_credit, make_flow

from krx_toss.strategy.signals import (
    evaluate_reversal_symbol,
    evaluate_symbol,
    index_blocks_entries,
    select_signals,
)
from krx_toss.strategy.universe import (
    blocked_warning_set,
    build_universe,
    merge_ranking_symbols,
    normalize_ranking_duration,
    warning_blocked,
)


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


def test_either_flow_accepts_foreign_only():
    start = date(2026, 1, 1)
    candles = make_candles(start, 30, drift=Decimal("40"))
    flow = make_flow(start, 30, foreign=1000, institution=-500)
    credit = make_credit(start, 30)
    params = {**PARAMS, "require_both_flows": False}
    signal, reason = evaluate_symbol(
        symbol="005930", market="KOSPI", candles=candles, flow=flow, credit=credit, params=params
    )
    assert reason is None
    assert signal is not None


def test_reversal_accepts_dip_even_when_flow_is_selling():
    start = date(2026, 1, 1)
    candles = make_candles(start, 30, drift=Decimal("40"))
    candles[-1].close = candles[-2].close * Decimal("0.95")
    candles[-1].low = candles[-1].close
    flow = make_flow(start, 30, foreign=-1000, institution=-500)
    credit = make_credit(start, 30)
    signal, reason = evaluate_reversal_symbol(
        symbol="005930", market="KOSPI", candles=candles, flow=flow, credit=credit, params=PARAMS
    )
    assert reason is None
    assert signal is not None
    assert "dip_reversal" in signal.reasons
    assert signal.ret_1d is not None and signal.ret_1d <= Decimal("-0.04")


def test_reversal_rejects_shallow_dip():
    start = date(2026, 1, 1)
    candles = make_candles(start, 30, drift=Decimal("40"))
    candles[-1].close = candles[-2].close * Decimal("0.99")
    flow = make_flow(start, 30, -1, -1)
    credit = make_credit(start, 30)
    _, reason = evaluate_reversal_symbol(
        symbol="005930", market="KOSPI", candles=candles, flow=flow, credit=credit, params=PARAMS
    )
    assert reason == "dip_too_small"


def test_select_signals_uses_reversal_when_kospi_drops():
    start = date(2026, 1, 1)
    candles = make_candles(start, 30, drift=Decimal("40"))
    candles[-1].close = candles[-2].close * Decimal("0.94")
    candles[-1].low = candles[-1].close
    kospi = make_candles(start, 30, start_px=Decimal("3000"), drift=Decimal("5"))
    kospi[-1].close = kospi[-2].close * Decimal("0.98")
    decision = select_signals(
        [("005930", "KOSPI", candles, make_flow(start, 30, -1, -1), make_credit(start, 30))],
        PARAMS,
        kospi_candles=kospi,
    )
    assert len(decision.accepted) == 1
    assert decision.accepted[0].reasons == ["dip_reversal"]


def test_reversal_always_catches_idiosyncratic_dump_on_up_kospi():
    start = date(2026, 1, 1)
    candles = make_candles(start, 30, drift=Decimal("40"))
    candles[-1].close = candles[-2].close * Decimal("0.97")
    candles[-1].low = candles[-1].close
    kospi = make_candles(start, 30, start_px=Decimal("3000"), drift=Decimal("10"))
    params = {**PARAMS, "reversal_always": True, "reversal_min_1d": "-0.015"}
    decision = select_signals(
        [("005930", "KOSPI", candles, make_flow(start, 30, -1, -1), make_credit(start, 30))],
        params,
        kospi_candles=kospi,
    )
    assert len(decision.accepted) == 1
    assert decision.accepted[0].reasons == ["dip_reversal"]


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


def test_ranking_duration_aliases_month():
    assert normalize_ranking_duration("1m") == "1mo"
    assert normalize_ranking_duration("1mo") == "1mo"
    assert normalize_ranking_duration("1w") == "1w"


def test_hard_excludes_apply_even_if_yaml_omits_them():
    blocked = blocked_warning_set([])
    assert "LIQUIDATION_TRADING" in blocked
    assert warning_blocked([{"warningType": "STOCK_WARRANTS"}], blocked)


def test_scan_skips_watchlist_fetch_when_kospi_risk_off(tmp_path):
    from krx_toss.backtest.cache import MarketCache
    from krx_toss.config import Settings
    from krx_toss.jobs.close_scan import scan_signals

    class DropClient:
        def __init__(self) -> None:
            self.rankings = 0

        def get_indicator_candles(self, *_args, **_kwargs):
            return {
                "candles": [
                    {
                        "timestamp": "2026-01-01T00:00:00+09:00",
                        "openPrice": "3000",
                        "highPrice": "3000",
                        "lowPrice": "2900",
                        "closePrice": "3000",
                        "volume": "1",
                    },
                    {
                        "timestamp": "2026-01-02T00:00:00+09:00",
                        "openPrice": "2700",
                        "highPrice": "2700",
                        "lowPrice": "2600",
                        "closePrice": "2700",
                        "volume": "1",
                    },
                ]
            }

        def get_rankings(self, **_kwargs):
            self.rankings += 1
            return {"rankings": []}

    settings = Settings(
        base_url="https://example.invalid",
        dry_run=True,
        account_seq=None,
        timezone="Asia/Seoul",
        http_timeout_seconds=5,
        token_refresh_skew_seconds=60,
        overlay_seconds=60,
        holdings_seconds=30,
        order_status_seconds=15,
        balance_update_seconds=1800,
        cache_dir=tmp_path,
        blotter_db=tmp_path / "b.sqlite",
        kill_switch=tmp_path / "kill.json",
        logs_dir=tmp_path,
        signals_path=tmp_path / "signals.json",
        creds_path=tmp_path / "creds.csv",
        nasang_token_path=tmp_path / "nasang",
        position_token_path=tmp_path / "position",
        telegram_chat_id=None,
        telegram_position_chat_id=None,
        strategy={"signal": {"kospi_skip_1d_return": "-0.02"}, "universe": {"watchlist_size": 10}},
        root=tmp_path,
    )
    client = DropClient()
    payload = scan_signals(client, settings, MarketCache(tmp_path))
    assert payload["rejected"] == {"KOSPI": "kospi_risk_off"}
    assert payload["accepted"] == []
    assert client.rankings == 0

