from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# Latest published defaults from Toss overview.md. Runtime headers override.
DEFAULT_TPS: dict[str, float] = {
    "AUTH": 5,
    "ACCOUNT": 1,
    "ASSET": 5,
    "STOCK": 5,
    "STOCK_ALL": 1,
    "STOCK_TRADING_TREND": 10,
    "MARKET_INFO": 3,
    "MARKET_DATA": 15,
    "MARKET_DATA_CHART": 20,
    "RANKING": 5,
    "MARKET_INDICATOR_PRICE": 10,
    "MARKET_INDICATOR": 10,
    "MARKET_INDICATOR_CHART": 5,
    "ORDER": 10,
    "ORDER_HISTORY": 5,
    "ORDER_INFO": 6,
    "CONDITIONAL_ORDER": 5,
    "CONDITIONAL_ORDER_HISTORY": 10,
}

PEAK_TPS: dict[str, float] = {
    "ORDER_INFO": 3,
}

ORDER_INFO_PEAK_START = dtime(9, 0)
ORDER_INFO_PEAK_END = dtime(9, 10)


def in_order_info_peak(now: datetime | None = None) -> bool:
    current = now.astimezone(KST) if now else datetime.now(KST)
    clock = current.time()
    return ORDER_INFO_PEAK_START <= clock < ORDER_INFO_PEAK_END


@dataclass
class _Bucket:
    tokens: float
    last: float
    capacity: float
    rate: float
    lock: threading.Lock


class RateLimiter:
    """Token-bucket limiter, one bucket per Toss rate-limit group.

    Toss publishes a per-second burst. Starting the bucket full and firing that
    many calls in one millisecond still 429s, so we keep a safety haircut and
    only allow one token up front.
    """

    def __init__(self, tps: dict[str, float] | None = None, *, safety: float = 0.5) -> None:
        self._defaults = dict(tps or DEFAULT_TPS)
        self._safety = min(1.0, max(0.1, safety))
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def _rate_for(self, group: str, now: datetime | None = None) -> float:
        if group == "ORDER_INFO" and in_order_info_peak(now):
            published = PEAK_TPS.get(group, self._defaults.get(group, 1.0))
        else:
            published = self._defaults.get(group, 1.0)
        return max(0.2, published * self._safety)

    def _caps(self, rate: float) -> tuple[float, float]:
        rate = max(0.2, rate)
        return max(1.0, rate), rate

    def _bucket(self, group: str) -> _Bucket:
        with self._lock:
            bucket = self._buckets.get(group)
            rate = self._rate_for(group)
            capacity, rate = self._caps(rate)
            if bucket is None:
                bucket = _Bucket(
                    tokens=1.0,
                    last=time.monotonic(),
                    capacity=capacity,
                    rate=rate,
                    lock=threading.Lock(),
                )
                self._buckets[group] = bucket
            elif group == "ORDER_INFO":
                bucket.capacity = capacity
                bucket.rate = rate
            return bucket

    def update_from_headers(self, group: str, limit: float | None) -> None:
        if limit is None or limit <= 0:
            return
        bucket = self._bucket(group)
        capacity, rate = self._caps(limit * self._safety)
        with bucket.lock:
            bucket.capacity = capacity
            bucket.rate = rate

    def set_remaining(self, group: str, remaining: float) -> None:
        bucket = self._bucket(group)
        with bucket.lock:
            if remaining < 1:
                bucket.tokens = 0.0
                bucket.last = time.monotonic()

    def note_throttle(self, group: str) -> None:
        bucket = self._bucket(group)
        with bucket.lock:
            bucket.tokens = 0.0
            bucket.last = time.monotonic()

    def acquire(self, group: str, *, timeout: float = 90.0) -> None:
        bucket = self._bucket(group)
        deadline = time.monotonic() + timeout
        while True:
            with bucket.lock:
                now = time.monotonic()
                elapsed = max(0.0, now - bucket.last)
                bucket.tokens = min(bucket.capacity, bucket.tokens + elapsed * bucket.rate)
                bucket.last = now
                if bucket.tokens >= 1.0:
                    bucket.tokens -= 1.0
                    return
                wait = (1.0 - bucket.tokens) / bucket.rate if bucket.rate else 0.2
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"rate limiter timeout for group {group}")
            time.sleep(min(wait, remaining, 1.0))
