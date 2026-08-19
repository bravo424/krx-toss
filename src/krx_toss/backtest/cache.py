from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from krx_toss.strategy.features import Candle, CreditDay, FlowDay, parse_candles, parse_credit, parse_flow
from krx_toss.toss.client import TossClient

log = logging.getLogger(__name__)


class MarketCache:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, kind: str, symbol: str) -> Path:
        safe = symbol.replace("/", "_")
        return self.cache_dir / f"{kind}_{safe}.parquet"

    def save_records(self, kind: str, symbol: str, rows: list[dict[str, Any]]) -> Path:
        path = self._path(kind, symbol)
        if not rows:
            pd.DataFrame().to_parquet(path)
            return path
        pd.DataFrame(rows).to_parquet(path, index=False)
        return path

    def load_records(self, kind: str, symbol: str) -> list[dict[str, Any]]:
        path = self._path(kind, symbol)
        if not path.exists():
            return []
        df = pd.read_parquet(path)
        return df.to_dict(orient="records")

    def write_json(self, name: str, payload: Any) -> Path:
        path = self.cache_dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return path

    def read_json(self, name: str) -> Any:
        path = self.cache_dir / name
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def fetch_symbol(self, client: TossClient, symbol: str, *, bars: int = 260) -> dict[str, list]:
        candle_rows: list[dict[str, Any]] = []
        flow_rows: list[dict[str, Any]] = []
        credit_rows: list[dict[str, Any]] = []
        try:
            candles = client.get_all_candles(symbol, interval="1d", max_bars=bars)
            candle_rows = candles if isinstance(candles, list) else []
        except Exception as exc:  # noqa: BLE001
            log.warning("candles failed %s: %s", symbol, exc)
        try:
            flow = client.get_investor_trading(symbol, count=60)
            flow_rows = list(flow.get("records") or flow.get("items") or flow.get("investorTrading") or [])
        except Exception as exc:  # noqa: BLE001
            log.warning("flow failed %s: %s", symbol, exc)
        try:
            credit = client.get_credit_trades(symbol, count=40)
            credit_rows = list(credit.get("records") or credit.get("items") or credit.get("creditTrades") or [])
        except Exception as exc:  # noqa: BLE001
            log.warning("credit failed %s: %s", symbol, exc)
        self.save_records("candles", symbol, candle_rows)
        self.save_records("flow", symbol, flow_rows)
        self.save_records("credit", symbol, credit_rows)
        return {"candles": candle_rows, "flow": flow_rows, "credit": credit_rows}

    def candles(self, symbol: str) -> list[Candle]:
        return parse_candles(self.load_records("candles", symbol))

    def flow(self, symbol: str) -> list[FlowDay]:
        return parse_flow(self.load_records("flow", symbol))

    def credit(self, symbol: str) -> list[CreditDay]:
        return parse_credit(self.load_records("credit", symbol))
