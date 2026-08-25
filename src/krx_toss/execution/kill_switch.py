from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


class KillSwitch:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def trip(self, reason: str) -> None:
        payload = {"tripped": True, "reason": reason, "on": datetime.now(KST).date().isoformat()}
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def reset(self) -> None:
        if self.path.exists():
            self.path.unlink()

    def status(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"tripped": False, "reason": None, "on": None}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"tripped": True, "reason": "corrupt_kill_switch_file", "on": None}
        tripped = bool(data.get("tripped"))
        on = data.get("on")
        today = datetime.now(KST).date().isoformat()
        if tripped and (not on or str(on) < today):
            self.reset()
            return {"tripped": False, "reason": None, "on": None}
        return {"tripped": tripped, "reason": data.get("reason"), "on": on}

    def tripped(self) -> bool:
        return bool(self.status()["tripped"])
