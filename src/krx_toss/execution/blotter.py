from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


def _adapt_decimal(value: Decimal) -> str:
    return str(value)


def _convert_ok() -> None:
    sqlite3.register_adapter(Decimal, _adapt_decimal)


_convert_ok()


class Blotter:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def close(self) -> None:
        self._conn.close()

    def _init(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS orders (
                client_order_id TEXT PRIMARY KEY,
                order_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price TEXT NOT NULL,
                status TEXT NOT NULL,
                dry_run INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                extra TEXT
            );
            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                quantity INTEGER NOT NULL,
                avg_price TEXT NOT NULL,
                market TEXT,
                opened_on TEXT,
                sessions_held INTEGER DEFAULT 0,
                oco_id TEXT,
                stop_price TEXT,
                take_profit_price TEXT
            );
            CREATE TABLE IF NOT EXISTS fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price TEXT NOT NULL,
                ts TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pnl_days (
                d TEXT PRIMARY KEY,
                realized TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def record_order(self, *, client_order_id: str, order_id: str | None, symbol: str, side: str, quantity: int, price: Decimal, status: str, dry_run: bool, extra: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO orders(client_order_id, order_id, symbol, side, quantity, price, status, dry_run, created_at, extra)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    client_order_id,
                    order_id,
                    symbol,
                    side,
                    quantity,
                    str(price),
                    status,
                    1 if dry_run else 0,
                    datetime.now(UTC).isoformat(),
                    json.dumps(extra or {}),
                ),
            )
            self._conn.commit()

    def upsert_position(
        self,
        symbol: str,
        quantity: int,
        avg_price: Decimal,
        market: str,
        opened_on: str,
        oco_id: str | None,
        stop_price: Decimal | None,
        take_profit_price: Decimal | None,
    ) -> None:
        with self._lock:
            if quantity <= 0:
                self._conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
            else:
                self._conn.execute(
                    """
                    INSERT INTO positions(symbol, quantity, avg_price, market, opened_on, sessions_held, oco_id, stop_price, take_profit_price)
                    VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        quantity=excluded.quantity,
                        avg_price=excluded.avg_price,
                        market=excluded.market,
                        oco_id=excluded.oco_id,
                        stop_price=excluded.stop_price,
                        take_profit_price=excluded.take_profit_price
                    """,
                    (
                        symbol,
                        quantity,
                        str(avg_price),
                        market,
                        opened_on,
                        oco_id,
                        None if stop_price is None else str(stop_price),
                        None if take_profit_price is None else str(take_profit_price),
                    ),
                )
            self._conn.commit()

    def bump_sessions(self) -> None:
        with self._lock:
            self._conn.execute("UPDATE positions SET sessions_held = sessions_held + 1")
            self._conn.commit()

    def positions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM positions").fetchall()
        return [dict(r) for r in rows]

    def position(self, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM positions WHERE symbol = ?", (symbol,)).fetchone()
        return dict(row) if row else None

    def pending_orders(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM orders WHERE status IN ('SUBMITTED', 'PARTIAL') AND dry_run = 0"
            ).fetchall()
        return [dict(r) for r in rows]

    def set_order_status(
        self,
        client_order_id: str,
        status: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT extra FROM orders WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
            if row is None:
                return
            merged: dict[str, Any] = json.loads(row["extra"] or "{}")
            if extra:
                merged.update(extra)
            self._conn.execute(
                "UPDATE orders SET status = ?, extra = ? WHERE client_order_id = ?",
                (status, json.dumps(merged), client_order_id),
            )
            self._conn.commit()

    def add_fill(self, symbol: str, side: str, quantity: int, price: Decimal) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO fills(symbol, side, quantity, price, ts) VALUES (?, ?, ?, ?, ?)",
                (symbol, side, quantity, str(price), datetime.now(UTC).isoformat()),
            )
            self._conn.commit()

    def fills(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM fills ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def add_realized(self, day: date, amount: Decimal) -> None:
        key = day.isoformat()
        with self._lock:
            row = self._conn.execute("SELECT realized FROM pnl_days WHERE d = ?", (key,)).fetchone()
            current = Decimal(row["realized"]) if row else Decimal("0")
            new = current + amount
            self._conn.execute("INSERT OR REPLACE INTO pnl_days(d, realized) VALUES (?, ?)", (key, str(new)))
            self._conn.commit()

    def realized_on(self, day: date) -> Decimal:
        with self._lock:
            row = self._conn.execute("SELECT realized FROM pnl_days WHERE d = ?", (day.isoformat(),)).fetchone()
        return Decimal(row["realized"]) if row else Decimal("0")
