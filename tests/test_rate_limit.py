from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from krx_toss.toss.rate_limit import RateLimiter, in_order_info_peak


def test_acquire_paces_instead_of_bursting():
    limiter = RateLimiter(tps={"STOCK": 10}, safety=0.5)
    started = time.monotonic()
    for _ in range(4):
        limiter.acquire("STOCK", timeout=5)
    elapsed = time.monotonic() - started
    # 0.5 safety → 5 TPS, first token is free, three more need ~0.6s
    assert elapsed >= 0.4


def test_unknown_group_defaults():
    limiter = RateLimiter()
    limiter.acquire("NOT_A_REAL_GROUP", timeout=5)


def test_peak_window_kst():
    kst = ZoneInfo("Asia/Seoul")
    assert in_order_info_peak(datetime(2026, 8, 14, 9, 5, tzinfo=kst))
    assert not in_order_info_peak(datetime(2026, 8, 14, 9, 15, tzinfo=kst))
    assert not in_order_info_peak(datetime(2026, 8, 14, 8, 59, tzinfo=kst))
