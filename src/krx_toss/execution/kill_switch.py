from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class KillSwitch:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def trip(self, reason: str) -> None:
        self.path.write_text(json.dumps({"tripped": True, "reason": reason}, ensure_ascii=False), encoding="utf-8")

    def reset(self) -> None:
        if self.path.exists():
            self.path.unlink()

    def status(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"tripped": False, "reason": None}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"tripped": True, "reason": "corrupt_kill_switch_file"}
        return {"tripped": bool(data.get("tripped")), "reason": data.get("reason")}

    def tripped(self) -> bool:
        return bool(self.status()["tripped"])
