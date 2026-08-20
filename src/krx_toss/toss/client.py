from __future__ import annotations

import logging
import random
import time
from collections.abc import Mapping
from typing import Any

import httpx

from krx_toss.toss.auth import TokenManager
from krx_toss.toss.errors import RateLimitExceeded, TossApiError
from krx_toss.toss.rate_limit import RateLimiter

log = logging.getLogger(__name__)

IDEMPOTENT_METHODS = {"GET", "HEAD", "OPTIONS"}
RETRY_STATUSES = {429, 500, 502, 503, 504}


class TossClient:
    def __init__(
        self,
        *,
        base_url: str,
        token_manager: TokenManager,
        limiter: RateLimiter | None = None,
        timeout: float = 20.0,
        account_seq: int | None = None,
        http: httpx.Client | None = None,
        max_retries: int = 4,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._tokens = token_manager
        self.limiter = limiter or RateLimiter()
        self.account_seq = account_seq
        self._http = http or httpx.Client(timeout=timeout, base_url=self.base_url)
        self._owns_http = http is None
        self._max_retries = max_retries

    def close(self) -> None:
        if self._owns_http:
            self._http.close()
        self._tokens.close()

    def __enter__(self) -> TossClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def set_account(self, account_seq: int) -> None:
        self.account_seq = account_seq

    def request(
        self,
        method: str,
        path: str,
        *,
        group: str,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        account: bool = False,
        idempotent: bool | None = None,
    ) -> Any:
        if account and self.account_seq is None:
            raise TossApiError("account_seq is required", code="account-header-required", status_code=400)

        can_retry = IDEMPOTENT_METHODS.__contains__(method.upper()) if idempotent is None else idempotent
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            self.limiter.acquire(group)
            headers = {
                "Authorization": f"Bearer {self._tokens.get_token()}",
                "Accept": "application/json",
            }
            if json is not None:
                headers["Content-Type"] = "application/json"
            if account:
                headers["X-Tossinvest-Account"] = str(self.account_seq)
            try:
                response = self._http.request(method, path, params=_clean_params(params), json=json, headers=headers)
            except httpx.HTTPError as exc:
                last_error = TossApiError(str(exc), status_code=None, code="transport")
                if not can_retry or attempt >= self._max_retries:
                    raise last_error from exc
                time.sleep(_backoff(attempt))
                continue

            self._apply_rate_headers(group, response)
            if response.status_code == 401:
                self._tokens.invalidate()
                if attempt < self._max_retries:
                    continue
            if response.status_code == 429:
                wait = _retry_after(response) or _backoff(attempt)
                self.limiter.note_throttle(group)
                last_error = RateLimitExceeded(
                    "rate limit exceeded",
                    status_code=429,
                    code=_error_code(response) or "rate-limit-exceeded",
                    request_id=response.headers.get("X-Request-Id"),
                    retry_after=wait,
                )
                if not can_retry or attempt >= self._max_retries:
                    raise last_error
                log.warning("Toss 429 on %s %s — waiting %.1fs then retry", group, path, wait)
                time.sleep(wait)
                continue
            if response.status_code >= 400:
                error = _raise_for_status(response)
                if can_retry and error.retryable and attempt < self._max_retries:
                    last_error = error
                    time.sleep(error.retry_after or _backoff(attempt))
                    continue
                raise error
            return _unwrap(response)

        assert last_error is not None
        raise last_error

    def _apply_rate_headers(self, group: str, response: httpx.Response) -> None:
        limit = response.headers.get("X-RateLimit-Limit")
        if limit:
            try:
                self.limiter.update_from_headers(group, float(limit))
            except ValueError:
                pass
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            try:
                self.limiter.set_remaining(group, float(remaining))
            except ValueError:
                pass

    # --- Auth / account ---
    def get_accounts(self) -> list[dict[str, Any]]:
        return _as_list(self.request("GET", "/api/v1/accounts", group="ACCOUNT"))

    def resolve_account(self) -> int:
        if self.account_seq is not None:
            return self.account_seq
        accounts = self.get_accounts()
        if not accounts:
            raise TossApiError("no accounts returned", code="account-not-found", status_code=404)
        brokerage = [a for a in accounts if str(a.get("accountType", "BROKERAGE")).upper() == "BROKERAGE"]
        chosen = brokerage[0] if brokerage else accounts[0]
        seq = int(chosen["accountSeq"])
        self.set_account(seq)
        return seq

    def get_holdings(self) -> dict[str, Any]:
        return _as_dict(self.request("GET", "/api/v1/holdings", group="ASSET", account=True))

    # --- Market data ---
    def get_prices(self, symbols: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for chunk in _chunks(symbols, 200):
            out.extend(
                _as_list(self.request("GET", "/api/v1/prices", group="MARKET_DATA", params={"symbols": ",".join(chunk)}))
            )
        return out

    def get_price_limits(self, symbol: str) -> dict[str, Any]:
        return _as_dict(self.request("GET", "/api/v1/price-limits", group="MARKET_DATA", params={"symbol": symbol}))

    def get_candles(
        self,
        symbol: str,
        interval: str = "1d",
        count: int = 100,
        before: str | None = None,
        adjusted: bool = True,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
            "count": min(count, 200),
            "adjusted": str(adjusted).lower(),
        }
        if before:
            params["before"] = before
        return _as_dict(self.request("GET", "/api/v1/candles", group="MARKET_DATA_CHART", params=params))

    def get_all_candles(self, symbol: str, interval: str = "1d", max_bars: int = 400, adjusted: bool = True) -> list[dict[str, Any]]:
        bars: list[dict[str, Any]] = []
        before = None
        while len(bars) < max_bars:
            page = self.get_candles(symbol, interval=interval, count=min(200, max_bars - len(bars)), before=before, adjusted=adjusted)
            candles = page.get("candles") or []
            bars.extend(candles)
            before = page.get("nextBefore")
            if not candles or not before:
                break
        return bars

    # --- Stock info ---
    def get_stocks(self, symbols: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for chunk in _chunks(symbols, 200):
            out.extend(
                _as_list(self.request("GET", "/api/v1/stocks", group="STOCK", params={"symbols": ",".join(chunk)}))
            )
        return out

    def get_warnings(self, symbol: str) -> list[dict[str, Any]]:
        return _as_list(self.request("GET", f"/api/v1/stocks/{symbol}/warnings", group="STOCK"))

    def get_investor_trading(self, symbol: str, count: int = 20, until: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"count": min(count, 100)}
        if until:
            params["until"] = until
        raw = self.request("GET", f"/api/v1/stocks/{symbol}/investor-trading", group="STOCK_TRADING_TREND", params=params)
        if isinstance(raw, list):
            return {"records": raw}
        return _as_dict(raw)

    def get_credit_trades(self, symbol: str, count: int = 20, until: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"count": min(count, 100)}
        if until:
            params["until"] = until
        raw = self.request("GET", f"/api/v1/stocks/{symbol}/credit-trades", group="STOCK_TRADING_TREND", params=params)
        if isinstance(raw, list):
            return {"records": raw}
        return _as_dict(raw)

    # --- Market info / indicators / ranking ---
    def get_kr_calendar(self, date: str | None = None) -> dict[str, Any]:
        params = {"date": date} if date else None
        return _as_dict(self.request("GET", "/api/v1/market-calendar/KR", group="MARKET_INFO", params=params))

    def get_rankings(
        self,
        ranking_type: str,
        market_country: str = "KR",
        duration: str = "1d",
        exclude_investment_caution: bool = True,
        count: int = 100,
    ) -> dict[str, Any]:
        return _as_dict(
            self.request(
                "GET",
                "/api/v1/rankings",
                group="RANKING",
                params={
                    "type": ranking_type,
                    "marketCountry": market_country,
                    "duration": duration,
                    "excludeInvestmentCaution": str(exclude_investment_caution).lower(),
                    "count": min(count, 100),
                },
            )
        )

    def get_indicator_prices(self, symbols: list[str]) -> list[dict[str, Any]]:
        return _as_list(
            self.request(
                "GET",
                "/api/v1/market-indicators/prices",
                group="MARKET_INDICATOR_PRICE",
                params={"symbols": ",".join(symbols)},
            )
        )

    def get_indicator_candles(self, symbol: str, interval: str = "1d", count: int = 30) -> dict[str, Any]:
        return _as_dict(
            self.request(
                "GET",
                f"/api/v1/market-indicators/{symbol}/candles",
                group="MARKET_INDICATOR_CHART",
                params={"interval": interval, "count": min(count, 200)},
            )
        )

    # --- Order info / orders ---
    def get_buying_power(self, currency: str = "KRW") -> dict[str, Any]:
        return _as_dict(
            self.request(
                "GET",
                "/api/v1/buying-power",
                group="ORDER_INFO",
                account=True,
                params={"currency": currency},
            )
        )

    def get_sellable_quantity(self, symbol: str) -> dict[str, Any]:
        return _as_dict(
            self.request("GET", "/api/v1/sellable-quantity", group="ORDER_INFO", account=True, params={"symbol": symbol})
        )

    def get_commissions(self) -> list[dict[str, Any]]:
        return _as_list(self.request("GET", "/api/v1/commissions", group="ORDER_INFO", account=True))

    def create_order(self, body: dict[str, Any]) -> dict[str, Any]:
        # Never auto-retry: POST without (or even with) a racing retry can double-fill.
        return _as_dict(
            self.request("POST", "/api/v1/orders", group="ORDER", json=body, account=True, idempotent=False)
        )

    def modify_order(self, order_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return _as_dict(
            self.request(
                "POST",
                f"/api/v1/orders/{order_id}/modify",
                group="ORDER",
                json=body,
                account=True,
                idempotent=False,
            )
        )

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return _as_dict(
            self.request(
                "POST",
                f"/api/v1/orders/{order_id}/cancel",
                group="ORDER",
                account=True,
                idempotent=False,
            )
        )

    def get_orders(
        self,
        status: str | None = "OPEN",
        *,
        symbol: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        cursor: str | None = None,
        limit: int | None = 100,
    ) -> dict[str, Any]:
        params = {
            "status": status,
            "symbol": symbol,
            "from": from_date,
            "to": to_date,
            "cursor": cursor,
            "limit": limit,
        }
        return _as_dict(self.request("GET", "/api/v1/orders", group="ORDER_HISTORY", account=True, params=params))

    def get_order(self, order_id: str) -> dict[str, Any]:
        return _as_dict(self.request("GET", f"/api/v1/orders/{order_id}", group="ORDER_HISTORY", account=True))

    def create_conditional_order(self, body: dict[str, Any]) -> dict[str, Any]:
        return _as_dict(
            self.request(
                "POST",
                "/api/v1/conditional-orders",
                group="CONDITIONAL_ORDER",
                json=body,
                account=True,
                idempotent=False,
            )
        )

    def cancel_conditional_order(self, conditional_order_id: str) -> None:
        self.request(
            "DELETE",
            f"/api/v1/conditional-orders/{conditional_order_id}",
            group="CONDITIONAL_ORDER",
            account=True,
            idempotent=False,
        )

    def get_conditional_orders(self, status: str = "OPEN") -> dict[str, Any]:
        return _as_dict(
            self.request(
                "GET",
                "/api/v1/conditional-orders",
                group="CONDITIONAL_ORDER_HISTORY",
                account=True,
                params={"status": status},
            )
        )


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _clean_params(params: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not params:
        return None
    return {k: v for k, v in params.items() if v is not None}


def _backoff(attempt: int) -> float:
    return min(8.0, (2**attempt)) + random.random() * 0.25


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict):
        code = err.get("code")
        return str(code) if code else None
    return None


def _raise_for_status(response: httpx.Response) -> TossApiError:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    err = payload.get("error") if isinstance(payload, dict) else {}
    if not isinstance(err, dict):
        err = {"message": str(payload)}
    message = err.get("message") or f"HTTP {response.status_code}"
    data = err.get("data")
    if data:
        message = f"{message} data={data}"
    return TossApiError(
        message,
        status_code=response.status_code,
        code=err.get("code"),
        request_id=response.headers.get("X-Request-Id") or err.get("requestId"),
        data=data,
        retry_after=_retry_after(response),
    )


def _unwrap(response: httpx.Response) -> Any:
    if response.status_code == 204 or not response.content:
        return None
    payload = response.json()
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


def _as_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, dict):
        for key in ("items", "stocks", "accounts", "rankings", "orders", "candles", "warnings", "commissions", "prices"):
            inner = value.get(key)
            if isinstance(inner, list):
                return [v for v in inner if isinstance(v, dict)]
        return [value]
    return []


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise TossApiError(f"expected object response, got {type(value).__name__}")
