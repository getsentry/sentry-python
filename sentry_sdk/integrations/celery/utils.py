from __future__ import annotations
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Tuple, List
    from sentry_sdk._types import MonitorConfigScheduleUnit


TIME_UNITS: List[Tuple[MonitorConfigScheduleUnit, float]] = [
    ("day", 60 * 60 * 24.0),
    ("hour", 60 * 60.0),
    ("minute", 60.0),
]


def _now_seconds_since_epoch() -> float:
    # We cannot use `time.perf_counter()` when dealing with the duration
    # of a Celery task, because the start of a Celery task and
    # the end are recorded in different processes.
    # Start happens in the Celery Beat process,
    # the end in a Celery Worker process.
    return time.time()


def _get_humanized_interval(seconds: float) -> Tuple[int, MonitorConfigScheduleUnit]:
    seconds = float(seconds)
    for unit, divider in TIME_UNITS:
        if seconds >= divider:
            interval = int(seconds / divider)
            return (interval, unit)

    return (int(seconds), "second")


class NoOpMgr:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        return None
