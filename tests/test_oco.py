from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from krx_toss.config import Settings
from krx_toss.cost.model import CostModel
from krx_toss.execution.blotter import Blotter
from krx_toss.execution.broker import Broker
from krx_toss.execution.kill_switch import KillSwitch
from krx_toss.jobs.open_entry import place_entries
from krx_toss.jobs.order_sync import sync_fills
from krx_toss.strategy.risk import OrderIntent, RiskLimits
from krx_toss.toss.errors import TossApiError


KST = ZoneInfo("Asia/Seoul")


class DummyClient:
    def __init__(self) -> None:
        self.orders: dict[str, dict] = {}
        self.created: list[dict] = []
        self.conditionals: list[dict] = []
        self.sellable: dict[str, int] = {}
        self.holdings: list[dict] = []
        self.oco_error: Exception | None = None
        self.prices: dict[str, str] = {}

    def create_order(self, body):
        self.created.append(body)
        order_id = f"oid-{len(self.created)}"
        self.orders[order_id] = {"status": "PENDING", "filledQuantity": "0"}
        return {"orderId": order_id, "clientOrderId": body["clientOrderId"]}

    def create_conditional_order(self, body):
        if self.oco_error:
            raise self.oco_error
        self.conditionals.append(body)
        return {"conditionalOrderId": f"oco-{len(self.conditionals)}"}

    def get_order(self, order_id):
        return self.orders.get(order_id, {"status": "OPEN"})

    def get_sellable_quantity(self, symbol):
        return {"sellableQuantity": str(self.sellable.get(symbol, 0))}

    def get_holdings(self):
        return {"items": self.holdings}

    def get_prices(self, symbols):
        return [{"symbol": s, "lastPrice": self.prices.get(s, "70000")} for s in symbols]

    def get_buying_power(self):
        return {"cashBuyingPower": "100000000"}


def _broker(tmp_path: Path, client: DummyClient, *, dry_run: bool = False) -> Broker:
    return Broker(
        client,
        Blotter(tmp_path / "b.sqlite"),
        KillSwitch(tmp_path / "kill.json"),
        dry_run=dry_run,
        cost=CostModel.from_strategy({}),
        limits=RiskLimits.from_strategy({"risk": {}, "exit": {"stop_loss": "0.04", "take_profit": "0.06"}}),
    )


def _buy_intent(symbol: str = "096770") -> OrderIntent:
    return OrderIntent(
        symbol=symbol,
        market="KOSPI",
        side="BUY",
        quantity=10,
        price=Decimal("130900"),
        confirm_high_value=False,
        notional=Decimal("1309000"),
        stop_price=Decimal("125664"),
        take_profit_price=Decimal("138754"),
    )


def test_live_buy_does_not_oco_before_fill(tmp_path: Path):
    client = DummyClient()
    broker = _broker(tmp_path, client)
    broker.submit_limit(_buy_intent())
    assert broker.ensure_oco("096770") is None
    assert client.conditionals == []
    assert broker.attach_oco(_buy_intent()) is None
    assert client.conditionals == []


def test_oco_without_holdings_does_not_raise(tmp_path: Path):
    client = DummyClient()
    client.oco_error = TossApiError("보유 수량이 없습니다. (종목코드: A096770)", status_code=422)
    client.sellable["096770"] = 10
    broker = _broker(tmp_path, client)
    broker.blotter.upsert_position(
        "096770", 10, Decimal("130900"), "KOSPI", "2026-08-19", None, Decimal("125664"), Decimal("138754")
    )
    assert broker.ensure_oco("096770") is None
    assert broker.blotter.position("096770")["oco_id"] is None


def test_fill_then_oco_when_sellable(tmp_path: Path):
    client = DummyClient()
    broker = _broker(tmp_path, client)
    broker.submit_limit(_buy_intent())
    order_id = next(iter(client.orders))
    client.orders[order_id] = {"status": "FILLED", "filledQuantity": "10", "averageFillPrice": "130900"}
    client.sellable["096770"] = 10
    events = sync_fills(broker)
    assert "096770:BUY:FILLED:10" in events
    assert "096770:OCO" in events
    assert len(client.conditionals) == 1
    assert client.conditionals[0]["first"]["orderSide"] == "SELL"
    pos = broker.blotter.position("096770")
    assert pos is not None
    assert pos["oco_id"] == "oco-1"


def _settings(tmp_path: Path) -> Settings:
    signals = tmp_path / "signals.json"
    signals.write_text(
        json.dumps(
            {
                "accepted": [
                    {
                        "symbol": "096770",
                        "market": "KOSPI",
                        "close": "130900",
                        "ma20": "119480",
                        "ret_20d": "0.1",
                        "ret_3d": "0.07",
                        "foreign_net": "1",
                        "institution_net": "1",
                    },
                    {
                        "symbol": "003230",
                        "market": "KOSPI",
                        "close": "1340000",
                        "ma20": "1222050",
                        "ret_20d": "0.2",
                        "ret_3d": "0.1",
                        "foreign_net": "1",
                        "institution_net": "1",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return Settings(
        base_url="https://example.invalid",
        dry_run=False,
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
        signals_path=signals,
        creds_path=tmp_path / "creds.csv",
        nasang_token_path=tmp_path / "nasang",
        position_token_path=tmp_path / "position",
        telegram_chat_id=None,
        telegram_position_chat_id=None,
        strategy={
            "entry": {"no_new_orders_until": "09:15", "limit_offset_ticks": 0},
            "exit": {"take_profit": "0.06", "stop_loss": "0.04"},
            "risk": {"max_positions": 8, "position_nav_pct": "0.10", "cash_buffer_pct": "0.20", "per_name_risk_pct": "0.02"},
        },
        root=tmp_path,
    )


def test_place_entries_skips_pending_buy_and_does_not_crash_on_oco(tmp_path: Path):
    client = DummyClient()
    client.oco_error = TossApiError("보유 수량이 없습니다. (종목코드: A096770)", status_code=422)
    client.prices = {"096770": "130900", "003230": "1340000"}
    broker = _broker(tmp_path, client)
    settings = _settings(tmp_path)
    now = datetime(2026, 8, 19, 10, 0, tzinfo=KST)
    first = place_entries(client, broker, settings, now=now)
    assert len(first) == 2
    assert len(client.created) == 2
    assert client.conditionals == []
    second = place_entries(client, broker, settings, now=now)
    assert second == []
    assert len(client.created) == 2
