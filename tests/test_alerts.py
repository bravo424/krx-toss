from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from krx_toss.alerts import TradingAlerts
from krx_toss.config import load_settings, load_token_file
from krx_toss.cost.model import CostModel
from krx_toss.execution.blotter import Blotter
from krx_toss.execution.broker import Broker
from krx_toss.execution.kill_switch import KillSwitch
from krx_toss.jobs.order_sync import sync_open_orders
from krx_toss.strategy.risk import OrderIntent, RiskLimits
from krx_toss.telegram_alerter import TelegramAlerter


class RecordingAlerter:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, text: str) -> None:
        self.messages.append(text)


class DummyClient:
    def __init__(self, orders: dict[str, dict] | None = None) -> None:
        self.orders = orders or {}
        self.created: list[dict] = []

    def create_order(self, body):
        self.created.append(body)
        return {"orderId": "oid-1", "clientOrderId": body["clientOrderId"]}

    def create_conditional_order(self, body):
        return {"conditionalOrderId": "oco-1"}

    def cancel_conditional_order(self, cid):
        return None

    def get_order(self, order_id):
        return self.orders.get(order_id, {"status": "OPEN"})

    def get_holdings(self):
        return {"items": []}

    def get_prices(self, symbols):
        return [{"symbol": s, "lastPrice": "70000"} for s in symbols]

    def get_stocks(self, symbols):
        names = {"005930": "삼성전자", "403870": "HPSP"}
        return [{"symbol": s, "name": names.get(s, "")} for s in symbols]


def _broker(tmp_path: Path, *, dry_run: bool, client=None, alerts=None) -> Broker:
    blotter = Blotter(tmp_path / "b.sqlite")
    kill = KillSwitch(tmp_path / "kill.json")
    return Broker(
        client or DummyClient(),
        blotter,
        kill,
        dry_run=dry_run,
        cost=CostModel.from_strategy({}),
        limits=RiskLimits.from_strategy({"risk": {}, "exit": {"stop_loss": "0.04"}}),
        alerts=alerts,
    )


def _buy_intent() -> OrderIntent:
    return OrderIntent(
        symbol="005930",
        market="KOSPI",
        side="BUY",
        quantity=10,
        price=Decimal("70000"),
        confirm_high_value=False,
        notional=Decimal("700000"),
        stop_price=Decimal("67200"),
        take_profit_price=Decimal("74200"),
    )


def test_from_env_requires_token_and_chat():
    assert TelegramAlerter.from_env(None, "1") is None
    assert TelegramAlerter.from_env("tok", None) is None
    alerter = TelegramAlerter.from_env(" tok ", " 99 ")
    assert alerter is not None
    assert alerter.token == "tok"
    assert alerter.chat_id == "99"


def test_load_token_file(tmp_path: Path):
    path = tmp_path / "token"
    path.write_text("abc\n", encoding="utf-8")
    assert load_token_file(path) == "abc"
    assert load_token_file(tmp_path / "missing") is None


def test_settings_load_telegram_from_project():
    settings = load_settings()
    assert settings.nasang_token_path.name == "nasang_bot_token"
    assert settings.position_token_path.name == "position_bot_token"
    assert settings.balance_update_seconds == 3600


def test_dry_run_buy_alerts_place_and_fill(tmp_path: Path):
    trade = RecordingAlerter()
    position = RecordingAlerter()
    alerts = TradingAlerts(trade=trade, position=position)
    broker = _broker(tmp_path, dry_run=True, alerts=alerts)
    broker.submit_limit(_buy_intent())
    assert any("Order placed" in m and "DRY RUN" in m and "005930 삼성전자" in m for m in trade.messages)
    assert any("Filled" in m and "BUY" in m for m in trade.messages)
    assert position.messages == []


def test_flatten_alerts_sell_place_and_close(tmp_path: Path):
    trade = RecordingAlerter()
    alerts = TradingAlerts(trade=trade)
    broker = _broker(tmp_path, dry_run=True, alerts=alerts)
    broker.blotter.upsert_position(
        "005930", 10, Decimal("70000"), "KOSPI", "2026-08-01", None, Decimal("67200"), Decimal("74200")
    )
    broker.flatten("005930", "KOSPI", Decimal("71000"), 10, "time_stop")
    assert any("Order placed" in m and "SELL" in m for m in trade.messages)
    assert any("Position closed" in m and "time_stop" in m for m in trade.messages)


def test_live_fill_sync_alerts_once(tmp_path: Path):
    trade = RecordingAlerter()
    alerts = TradingAlerts(trade=trade)
    client = DummyClient(
        orders={"oid-1": {"status": "FILLED", "filledQuantity": "10", "averageFillPrice": "70100"}}
    )
    broker = _broker(tmp_path, dry_run=False, client=client, alerts=alerts)
    broker.submit_limit(_buy_intent())
    assert any("Order placed" in m and "DRY RUN" not in m for m in trade.messages)
    assert not any("Filled" in m for m in trade.messages)
    events = sync_open_orders(broker)
    assert events == ["005930:BUY:FILLED:10"]
    assert any("Filled" in m and "005930" in m for m in trade.messages)
    pos = broker.blotter.position("005930")
    assert pos is not None
    assert int(pos["quantity"]) == 10
    assert sync_open_orders(broker) == []


def test_balance_update_goes_to_position_bot():
    trade = RecordingAlerter()
    position = RecordingAlerter()
    alerts = TradingAlerts(trade=trade, position=position)
    alerts.balance_update(
        cash=Decimal("50000000"),
        nav=Decimal("100000000"),
        positions=[
            {
                "symbol": "005930",
                "quantity": 10,
                "avg_price": "70000",
            }
        ],
        realized_today=Decimal("0"),
        marks={"005930": Decimal("71000")},
        names={"005930": "삼성전자"},
    )
    assert trade.messages == []
    assert len(position.messages) == 1
    body = position.messages[0]
    assert "krx-toss · hourly update" in body
    assert "₩100,000,000" in body
    assert "005930 삼성전자" in body
    assert "No open positions." not in body


def test_marked_equity_uses_last_price_not_cost():
    from krx_toss.jobs.telegram_job import marked_equity

    cash = Decimal("2462326")
    positions = [
        {"symbol": "096770", "quantity": 2, "avg_price": "130000"},
        {"symbol": "003230", "quantity": 1, "avg_price": "1330000"},
    ]
    cost_nav = marked_equity(cash, positions, {})
    assert cost_nav == Decimal("2462326") + Decimal("260000") + Decimal("1330000")
    marked = marked_equity(cash, positions, {"096770": Decimal("131500"), "003230": Decimal("1400000")})
    assert marked == Decimal("2462326") + Decimal("263000") + Decimal("1400000")
    assert marked != cost_nav


def test_scan_complete_lists_accepted():
    trade = RecordingAlerter()
    alerts = TradingAlerts(trade=trade)
    alerts.scan_complete(
        {
            "accepted": [
                {"symbol": "096770", "market": "KOSPI", "close": "130900"},
                {"symbol": "003230", "market": "KOSPI", "close": "1340000"},
            ],
            "rejected": {"005930": "weak_20d_return"},
            "universe": [
                {"symbol": "096770", "market": "KOSPI", "name": "SK Innovation"},
                {"symbol": "003230", "market": "KOSPI", "name": "Samyang Foods"},
            ],
        },
        source="scan",
    )
    assert len(trade.messages) == 1
    body = trade.messages[0]
    assert "krx-toss · scan" in body
    assert "Accepted <b>2</b>" in body
    assert "096770 SK Innovation" in body
    assert "003230 Samyang Foods" in body
    assert "₩130,900" in body


def test_scan_complete_empty():
    trade = RecordingAlerter()
    alerts = TradingAlerts(trade=trade)
    alerts.scan_complete({"accepted": [], "rejected": {"005930": "below_ma"}, "universe": []}, source="fetch-cache")
    assert "No names passed" in trade.messages[0]
    assert "fetch-cache" in trade.messages[0]


def test_market_open_and_close_go_to_trade_bot():
    trade = RecordingAlerter()
    position = RecordingAlerter()
    alerts = TradingAlerts(trade=trade, position=position)
    alerts.market_open(start="09:00", end="15:30", dry_run=False)
    alerts.market_close(start="09:00", end="15:30", dry_run=False)
    assert position.messages == []
    assert any("market open" in m and "09:00–15:30" in m for m in trade.messages)
    assert any("market closed" in m for m in trade.messages)


def test_crash_goes_to_both_bots():
    trade = RecordingAlerter()
    position = RecordingAlerter()
    alerts = TradingAlerts(trade=trade, position=position)
    alerts.crashed(RuntimeError("oco rejected"))
    assert any("CRASHED" in m and "oco rejected" in m for m in trade.messages)
    assert any("CRASHED" in m for m in position.messages)
