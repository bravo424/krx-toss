from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def project_root(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit
    return ROOT


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def load_creds(path: Path) -> tuple[str, str]:
    if not path.exists():
        example = path.with_name("creds.csv.example")
        raise FileNotFoundError(
            f"Missing {path}. Copy {example} to {path} and fill client_id/client_secret."
        )
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(lines) < 2:
        raise ValueError(f"{path} must have a header and one data row")
    header = [c.strip() for c in lines[0].split(",")]
    row = [c.strip() for c in lines[1].split(",")]
    mapping = dict(zip(header, row, strict=False))
    client_id = mapping.get("client_id") or (row[0] if row else "")
    client_secret = mapping.get("client_secret") or (row[1] if len(row) > 1 else "")
    if not client_id or not client_secret or client_id.startswith("YOUR_"):
        raise ValueError(f"Fill real client_id and client_secret in {path}")
    return client_id, client_secret


@dataclass(frozen=True)
class Settings:
    base_url: str
    dry_run: bool
    account_seq: int | None
    timezone: str
    http_timeout_seconds: float
    token_refresh_skew_seconds: int
    overlay_seconds: int
    holdings_seconds: int
    order_status_seconds: int
    balance_update_seconds: int
    cache_dir: Path
    blotter_db: Path
    kill_switch: Path
    logs_dir: Path
    signals_path: Path
    creds_path: Path
    nasang_token_path: Path
    position_token_path: Path
    telegram_chat_id: str | None
    telegram_position_chat_id: str | None
    strategy: dict[str, Any]
    root: Path

    def strategy_section(self, name: str) -> dict[str, Any]:
        section = self.strategy.get(name, {})
        if not isinstance(section, dict):
            raise TypeError(f"strategy.{name} must be a mapping")
        return section


def load_settings(root: Path | None = None) -> Settings:
    root = project_root(root)
    raw = load_yaml(root / "config" / "settings.yaml")
    strategy = load_yaml(root / "config" / "strategy.yaml")
    paths = raw.get("paths") or {}
    poll = raw.get("poll") or {}

    def resolve(rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else root / p

    account_seq = raw.get("account_seq")
    telegram = raw.get("telegram") or {}
    nasang_token = Path(os.getenv("TELEGRAM_CREDENTIALS_FILE") or telegram.get("nasang_token_path") or "config/nasang_bot_token")
    position_token = Path(
        os.getenv("POSITION_BOT_CREDENTIALS_FILE") or telegram.get("position_token_path") or "config/position_bot_token"
    )
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or telegram.get("chat_id") or None
    position_chat_id = os.getenv("POSITION_BOT_CHAT_ID") or telegram.get("position_chat_id") or chat_id
    return Settings(
        base_url=str(raw.get("base_url", "https://openapi.tossinvest.com")).rstrip("/"),
        dry_run=bool(raw.get("dry_run", True)),
        account_seq=int(account_seq) if account_seq is not None else None,
        timezone=str(raw.get("timezone", "Asia/Seoul")),
        http_timeout_seconds=float(raw.get("http_timeout_seconds", 20)),
        token_refresh_skew_seconds=int(raw.get("token_refresh_skew_seconds", 60)),
        overlay_seconds=int(poll.get("overlay_seconds", 60)),
        holdings_seconds=int(poll.get("holdings_seconds", 30)),
        order_status_seconds=int(poll.get("order_status_seconds", 15)),
        balance_update_seconds=int(poll.get("balance_update_seconds", telegram.get("balance_update_seconds", 3600))),
        cache_dir=resolve(paths.get("cache_dir", "data/cache")),
        blotter_db=resolve(paths.get("blotter_db", "data/blotter.sqlite")),
        kill_switch=resolve(paths.get("kill_switch", "data/kill_switch.json")),
        logs_dir=resolve(paths.get("logs_dir", "logs")),
        signals_path=resolve(paths.get("signals_path", "data/cache/signals.json")),
        creds_path=root / "config" / "creds.csv",
        nasang_token_path=nasang_token if nasang_token.is_absolute() else root / nasang_token,
        position_token_path=position_token if position_token.is_absolute() else root / position_token,
        telegram_chat_id=str(chat_id).strip() if chat_id else None,
        telegram_position_chat_id=str(position_chat_id).strip() if position_chat_id else None,
        strategy=strategy,
        root=root,
    )


def load_token_file(path: Path) -> str | None:
    if not path.exists():
        return None
    token = path.read_text(encoding="utf-8").strip()
    return token or None
