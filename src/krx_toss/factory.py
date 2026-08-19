from __future__ import annotations

from krx_toss.alerts import TradingAlerts
from krx_toss.config import Settings, load_creds, load_token_file
from krx_toss.telegram_alerter import TelegramAlerter
from krx_toss.toss.auth import TokenManager
from krx_toss.toss.client import TossClient
from krx_toss.toss.rate_limit import RateLimiter


def build_client(settings: Settings) -> TossClient:
    client_id, client_secret = load_creds(settings.creds_path)
    limiter = RateLimiter()
    tokens = TokenManager(
        base_url=settings.base_url,
        client_id=client_id,
        client_secret=client_secret,
        limiter=limiter,
        timeout=settings.http_timeout_seconds,
        refresh_skew=settings.token_refresh_skew_seconds,
    )
    return TossClient(
        base_url=settings.base_url,
        token_manager=tokens,
        limiter=limiter,
        timeout=settings.http_timeout_seconds,
        account_seq=settings.account_seq,
    )


def build_alerts(settings: Settings) -> TradingAlerts:
    trade = TelegramAlerter.from_env(
        token=load_token_file(settings.nasang_token_path),
        chat_id=settings.telegram_chat_id,
    )
    position = TelegramAlerter.from_env(
        token=load_token_file(settings.position_token_path),
        chat_id=settings.telegram_position_chat_id,
    )
    return TradingAlerts(trade=trade, position=position)
