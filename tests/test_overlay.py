from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from krx_toss.cost.model import CostModel
from krx_toss.execution.blotter import Blotter
from krx_toss.execution.broker import Broker
from krx_toss.execution.kill_switch import KillSwitch
from krx_toss.execution.overlay import lock_stop_price, overlay_actions, should_flatten_for_limit
from krx_toss.strategy.risk import RiskLimits


class DummyClient:
    def create_order(self, body):
        return {"orderId": "x"}

    def create_conditional_order(self, body):
        return {"conditionalOrderId": "oco"}

    def cancel_conditional_order(self, cid):
        return None

    def modify_conditional_order(self, cid, body):
        return {"conditionalOrderId": f"mod-{cid}"}


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


def test_lock_stop_price_ratchets_once():
    avg = Decimal("70000")
    tp = Decimal("75600")
    assert (
        lock_stop_price(
            avg=avg,
            last=Decimal("74100"),
            current_stop=Decimal("67200"),
            take_profit=tp,
            lock_pct=Decimal("0.06"),
            market="KOSPI",
        )
        is None
    )
    locked = lock_stop_price(
        avg=avg,
        last=Decimal("74200"),
        current_stop=Decimal("67200"),
        take_profit=tp,
        lock_pct=Decimal("0.06"),
        market="KOSPI",
    )
    assert locked == Decimal("74200")
    assert (
        lock_stop_price(
            avg=avg,
            last=Decimal("75000"),
            current_stop=locked,
            take_profit=tp,
            lock_pct=Decimal("0.06"),
            market="KOSPI",
        )
        is None
    )
    assert (
        lock_stop_price(
            avg=avg,
            last=Decimal("75600"),
            current_stop=Decimal("67200"),
            take_profit=tp,
            lock_pct=Decimal("0.06"),
            market="KOSPI",
        )
        is None
    )


def test_overlay_locks_profit_and_keeps_take_profit(tmp_path: Path):
    blotter = Blotter(tmp_path / "b.sqlite")
    kill = KillSwitch(tmp_path / "kill.json")
    broker = Broker(
        DummyClient(),
        blotter,
        kill,
        dry_run=True,
        cost=CostModel.from_strategy({}),
        limits=RiskLimits.from_strategy({"risk": {}, "exit": {"stop_loss": "0.04", "take_profit": "0.08"}}),
    )
    blotter.upsert_position(
        "005930", 10, Decimal("70000"), "KOSPI", "2026-08-01", "dry-oco-1", Decimal("67200"), Decimal("75600")
    )
    kwargs = dict(
        broker=broker,
        symbol="005930",
        market="KOSPI",
        warnings=[],
        price_limits={"upperLimitPrice": "100000"},
        near_limit_pct=Decimal("0.02"),
        flatten_on_vi=True,
        blocked_warnings={"INVESTMENT_WARNING"},
        lock_profit_pct=Decimal("0.06"),
    )
    reason = overlay_actions(last_price=Decimal("74200"), **kwargs)
    assert reason == "lock_profit"
    pos = blotter.position("005930")
    assert pos is not None
    assert pos["stop_price"] == "74200"
    assert pos["take_profit_price"] == "75600"
    assert overlay_actions(last_price=Decimal("75000"), **kwargs) is None
    assert blotter.position("005930")["stop_price"] == "74200"


def test_overlay_raises_old_take_profit_before_lock(tmp_path: Path):
    blotter = Blotter(tmp_path / "b.sqlite")
    kill = KillSwitch(tmp_path / "kill.json")
    broker = Broker(
        DummyClient(),
        blotter,
        kill,
        dry_run=True,
        cost=CostModel.from_strategy({}),
        limits=RiskLimits.from_strategy({"risk": {}, "exit": {"stop_loss": "0.04", "take_profit": "0.08"}}),
    )
    blotter.upsert_position(
        "005930", 10, Decimal("70000"), "KOSPI", "2026-08-01", "dry-oco-1", Decimal("67200"), Decimal("74200")
    )
    kwargs = dict(
        broker=broker,
        symbol="005930",
        market="KOSPI",
        warnings=[],
        price_limits={"upperLimitPrice": "100000"},
        near_limit_pct=Decimal("0.02"),
        flatten_on_vi=True,
        blocked_warnings={"INVESTMENT_WARNING"},
        lock_profit_pct=Decimal("0.06"),
    )
    assert overlay_actions(last_price=Decimal("71000"), **kwargs) == "raise_tp"
    pos = blotter.position("005930")
    assert pos is not None
    assert pos["stop_price"] == "67200"
    assert pos["take_profit_price"] == "75600"
    assert overlay_actions(last_price=Decimal("74200"), **kwargs) == "lock_profit"
    pos = blotter.position("005930")
    assert pos is not None
    assert pos["stop_price"] == "74200"
    assert pos["take_profit_price"] == "75600"
