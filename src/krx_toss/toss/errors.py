from __future__ import annotations

from typing import Any


class TossApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        request_id: str | None = None,
        data: Any = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.request_id = request_id
        self.data = data
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        if self.status_code in {429, 500, 502, 503, 504}:
            return True
        return self.code in {"already-processing", "request-in-progress", "internal-error", "maintenance"}

    @property
    def is_kill_switch(self) -> bool:
        return self.code == "maintenance" or self.status_code in {500, 502, 503}


class RateLimitExceeded(TossApiError):
    pass
