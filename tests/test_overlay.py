from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from krx_toss.cost.model import CostModel
from krx_toss.execution.blotter import Blotter
from krx_toss.execution.broker import Broker
from krx_toss.execution.kill_switch import KillSwitch
from krx_toss.execution.overlay import overlay_actions, should_flatten_for_limit
from krx_toss.strategy.risk import RiskLimits


class DummyClient:
    def create_order(self, body):
        return {"orderId": "x"}

    def create_conditional_order(self, body):
        return {"conditionalOrderId": "oco"}

    def cancel_conditional_order(self, cid):
        return None


def test_near_limit_flatten(tmp_path: Path):
    assert should_flatten_for_limit(Decimal("980"), Decimal("1000"), Decimal("0.03"))
    assert not should_flatten_for_limit(Decimal("900"), Decimal("1000"), Decimal("0.03"))

    blotter = Blotter(tmp_path / "b.sqlite")
    kill = KillSwitch(tmp_path / "kill.json")
    broker = Broker(
        DummyClient(),
        blotter,
        kill,
        dry_run=True,
        cost=CostModel.from_strategy({}),
        limits=RiskLimits.from_strategy({"risk": {}, "exit": {"stop_loss": "0.04"}}),
    )
    blotter.upsert_position("005930", 10, Decimal("70000"), "KOSPI", "2026-08-01", None, Decimal("67000"), Decimal("75000"))
    reason = overlay_actions(
        broker=broker,
        symbol="005930",
        market="KOSPI",
        last_price=Decimal("90000"),
        warnings=[{"warningType": "VI_DYNAMIC"}],
        price_limits={"upperLimitPrice": "100000"},
        near_limit_pct=Decimal("0.02"),
        flatten_on_vi=True,
        blocked_warnings={"INVESTMENT_WARNING"},
    )
    assert reason == "vi_active"
    assert blotter.position("005930") is None
