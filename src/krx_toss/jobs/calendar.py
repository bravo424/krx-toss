from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from krx_toss.strategy.risk import parse_hhmm

KST = ZoneInfo("Asia/Seoul")


def calendar_is_open(payload: dict[str, Any], day: datetime | None = None) -> bool:
    current = (day or datetime.now(KST)).astimezone(KST).date().isoformat()
    today = payload.get("today") or payload.get("current") or {}
    if isinstance(today, dict):
        date_val = str(today.get("date") or "")[:10]
        if date_val and date_val != current:
            # still treat as today block if API date matches session
            pass
        integrated = today.get("integrated")
        if integrated is None and "regularMarket" not in today:
            return False
        if integrated is False or integrated is None:
            # holiday when integrated is null per Toss docs
            if today.get("date") and integrated is None and not today.get("regularMarket"):
                return False
        regular = (integrated or {}).get("regularMarket") if isinstance(integrated, dict) else today.get("regularMarket")
        return bool(regular)
    return True


def regular_session_times(payload: dict[str, Any]) -> tuple[str, str]:
    today = payload.get("today") or {}
    integrated = today.get("integrated") if isinstance(today, dict) else {}
    regular: Any = {}
    if isinstance(integrated, dict):
        regular = integrated.get("regularMarket") or {}
    elif isinstance(today, dict):
        regular = today.get("regularMarket") or {}
    start_raw = end_raw = None
    if isinstance(regular, dict):
        start_raw = regular.get("startTime")
        end_raw = regular.get("endTime")
    start = parse_hhmm(str(start_raw) if start_raw not in (None, "") else "09:00", default="09:00")
    end = parse_hhmm(str(end_raw) if end_raw not in (None, "") else "15:30", default="15:30")
    return start.strftime("%H:%M"), end.strftime("%H:%M")
