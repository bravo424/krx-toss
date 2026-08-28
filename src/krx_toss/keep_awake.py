from __future__ import annotations

import logging
import sys
from types import TracebackType

log = logging.getLogger(__name__)

# MSDN SetThreadExecutionState
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_AWAYMODE_REQUIRED = 0x00000040
_FLAGS = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED


class KeepAwake:
    """Ask Windows not to idle-sleep while the scheduler is running.

    Lid-close and Hibernate can still freeze the process; those need the
    power plan (Sleep = Never, lid = Do nothing) while trading.
    """

    def __init__(self) -> None:
        self._armed = False

    def __enter__(self) -> KeepAwake:
        self._arm()
        return self

    def ping(self) -> None:
        if self._armed:
            self._arm()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self._armed:
            return
        self._armed = False
        if sys.platform != "win32":
            return
        try:
            import ctypes

            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception as err:  # noqa: BLE001
            log.warning("could not restore Windows sleep: %s", err)

    def _arm(self) -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes

            result = ctypes.windll.kernel32.SetThreadExecutionState(_FLAGS)
        except Exception as err:  # noqa: BLE001
            log.warning("could not prevent Windows sleep: %s", err)
            self._armed = False
            return
        if not result:
            log.warning("could not prevent Windows sleep (SetThreadExecutionState failed)")
            self._armed = False
            return
        if not self._armed:
            log.info("Windows sleep inhibited while scheduler runs")
        self._armed = True
