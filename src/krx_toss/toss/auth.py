from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import httpx

from krx_toss.toss.errors import TossApiError
from krx_toss.toss.rate_limit import RateLimiter


@dataclass
class Token:
    access_token: str
    token_type: str
    expires_at: float


class TokenManager:
    def __init__(
        self,
        *,
        base_url: str,
        client_id: str,
        client_secret: str,
        limiter: RateLimiter,
        timeout: float,
        refresh_skew: int,
        http: httpx.Client | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/oauth2/token"
        self._client_id = client_id
        self._client_secret = client_secret
        self._limiter = limiter
        self._timeout = timeout
        self._refresh_skew = refresh_skew
        self._http = http or httpx.Client(timeout=timeout)
        self._owns_http = http is None
        self._lock = threading.Lock()
        self._token: Token | None = None

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def get_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token.expires_at - self._refresh_skew:
                return self._token.access_token
            self._limiter.acquire("AUTH")
            response = self._http.post(
                self._url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            payload = _safe_json(response)
            if response.status_code >= 400:
                err = payload.get("error") if isinstance(payload, dict) else {}
                if not isinstance(err, dict):
                    err = {"message": str(payload)}
                raise TossApiError(
                    err.get("message") or f"token request failed: {response.status_code}",
                    status_code=response.status_code,
                    code=err.get("code") if isinstance(err.get("code"), str) else None,
                    request_id=response.headers.get("X-Request-Id"),
                )
            body = payload.get("result", payload) if isinstance(payload, dict) else {}
            access = body.get("access_token") or body.get("accessToken")
            if not access:
                raise TossApiError("token response missing access_token", status_code=response.status_code)
            expires_in = float(body.get("expires_in") or body.get("expiresIn") or 3600)
            self._token = Token(
                access_token=str(access),
                token_type=str(body.get("token_type") or body.get("tokenType") or "Bearer"),
                expires_at=time.time() + expires_in,
            )
            return self._token.access_token

    def invalidate(self) -> None:
        with self._lock:
            self._token = None


def _safe_json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
