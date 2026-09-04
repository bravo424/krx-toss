from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from helpers import make_candles, make_credit, make_flow

from krx_toss.agents.research_sweep import (
    apply_strategy_patch,
    build_sweep_variants,
    pick_alpha_candidate,
    run_variant_backtest,
    signal_snapshot_analysis,
)
from krx_toss.backtest.engine import SymbolHistory
from krx_toss.config import load_settings


def test_build_sweep_variants_includes_baseline_and_require_both_flows():
    base = {
        "signal": {"max_3d_return": 0.20, "require_both_flows": False},
        "exit": {"stop_loss": 0.04},
    }
    brief = {
        "suggested_param_sweeps": [
            {"section": "signal", "key": "max_3d_return", "range": [0.13, 0.20]},
            {"section": "exit", "key": "stop_loss", "range": [0.03, 0.05]},
        ]
    }
    variants = build_sweep_variants(base, brief)
    labels = [v["label"] for v in variants]
    assert labels[0] == "baseline"
    assert any("require_both_flows=True" in label for label in labels)
    assert any("max_3d_return" in label for label in labels)


def test_pick_alpha_candidate_requires_margin_and_trades():
    baseline = {"total_return": "0.05", "trades": 20}
    variants = [
        {"label": "baseline", "total_return": "0.05", "trades": 20},
        {"label": "weak", "total_return": "0.051", "trades": 20},
        {"label": "strong", "total_return": "0.08", "trades": 18},
        {"label": "empty", "total_return": "0.20", "trades": 0},
    ]
    best = pick_alpha_candidate(baseline, variants)
    assert best is not None
    assert best["label"] == "strong"


def test_run_variant_backtest_smoke():
    start = date(2026, 1, 1)
    candles = make_candles(start, 50, start_px=Decimal("10000"), drift=Decimal("80"))
    flow = make_flow(start, 50, 8000, 4000)
    credit = make_credit(start, 50, 1000)
    kospi = make_candles(start, 50, start_px=Decimal("2500"), drift=Decimal("5"))
    hist = {
        "005930": SymbolHistory(
            market="KOSPI",
            candles=candles,
            flow=flow,
            credit=credit,
            shares_outstanding=Decimal("5000000000"),
        )
    }
    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root)
    strategy = {
        **settings.strategy,
        "signal": {
            **settings.strategy.get("signal", {}),
            "max_3d_return": "0.50",
            "min_20d_return": 0,
        },
        "exit": {"stop_loss": "0.20", "take_profit": "0.50", "lock_profit": "0", "time_stop_sessions": 8},
        "risk": {
            **settings.strategy.get("risk", {}),
            "max_positions": 4,
            "per_name_risk_pct": "0.05",
        },
    }
    result = run_variant_backtest(settings, nav=Decimal("100000000"), histories=hist, kospi=kospi, strategy=strategy)
    assert result.end_nav > 0


def test_signal_snapshot_analysis(tmp_path: Path):
    signals = tmp_path / "signals.json"
    signals.write_text(
        """
        {
          "accepted": [
            {"symbol": "A", "reasons": ["above_ma"], "ret_3d": "0.25"},
            {"symbol": "B", "reasons": ["dip_reversal"], "ret_3d": "0.30"}
          ],
          "rejected": {"C": "overextended_3d"}
        }
        """,
        encoding="utf-8",
    )
    out = signal_snapshot_analysis(signals, {"signal": {"max_3d_return": 0.20}})
    assert out["accepted_total"] == 2
    assert out["momentum_accepted"] == 1
    assert out["dip_reversal_accepted"] == 1
    assert out["momentum_would_drop_if_max_3d"]["0.13"] == 1


def test_apply_strategy_patch_nested():
    base = {"signal": {"max_3d_return": 0.2, "ma_window": 20}}
    patched = apply_strategy_patch(base, {"signal": {"max_3d_return": 0.13}})
    assert patched["signal"]["max_3d_return"] == 0.13
    assert patched["signal"]["ma_window"] == 20
