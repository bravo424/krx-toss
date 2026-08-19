from __future__ import annotations

import logging
from pathlib import Path

from krx_toss.config import Settings


def setup_logging(settings: Settings) -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(Path(settings.logs_dir) / "krx-toss.log", encoding="utf-8"),
        ],
        force=True,
    )
