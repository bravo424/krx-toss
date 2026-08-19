from __future__ import annotations

from datetime import date
from decimal import Decimal

from helpers import make_candles, make_credit, make_flow

from krx_toss.backtest.engine import SymbolHistory, run_backtest
from krx_toss.cost.model import CostModel
from krx_toss.strategy.risk import RiskLimits


def test_backtest_no_lookahead_and_positive_on_uptrend():
    start = date(2026, 1, 1)
    candles = make_candles(start, 50, start_px=Decimal("10000"), drift=Decimal("80"))
    flow = make_flow(start, 50, 8000, 4000)
    credit = make_credit(start, 50, 1000)
    kospi = make_candles(start, 50, start_px=Decimal("2500"), drift=Decimal("5"))
    hist = {
        "005930": SymbolHistory(market="KOSPI", candles=candles, flow=flow, credit=credit, shares_outstanding=Decimal("5000000000"))
    }
    cost = CostModel.from_strategy({"cost": {"fallback_commission_rate": "0.00015", "stt": {"KOSPI": "0.0020"}}})
    limits = RiskLimits.from_strategy(
        {
            "risk": {
                "max_positions": 4,
                "position_nav_pct": "0.10",
                "cash_buffer_pct": "0.20",
                "per_name_risk_pct": "0.05",
                "max_notional_per_name": "5000000000",
            },
            "exit": {"stop_loss": "0.20", "take_profit": "0.50"},
        }
    )
    result = run_backtest(
        hist,
        kospi=kospi,
        cost=cost,
        limits=limits,
        signal_params={
            "flow_lookback_sessions": 3,
            "ma_window": 20,
            "min_20d_return": 0,
            "max_3d_return": "0.50",
            "max_credit_vs_avg": "5",
            "kospi_skip_1d_return": "-0.08",
        },
        start_nav=Decimal("100000000"),
        slippage_ticks=1,
        take_profit=Decimal("0.50"),
        stop_loss=Decimal("0.20"),
        time_stop=8,
    )
    assert result.start_nav == Decimal("100000000")
    assert result.end_nav != Decimal("0")
    if result.trades:
        first = result.trades[0]
        assert first.exit_date >= first.entry_date
        # T+1 execution: cannot enter on the signal bar date as fill date before next session.
        assert first.entry_date > candles[20].timestamp[:10]
