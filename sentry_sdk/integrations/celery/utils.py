import time

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


class NoOpMgr:
    def __enter__(self):
        # type: () -> None
        return None

    def __exit__(self, exc_type, exc_value, traceback):
        # type: (Any, Any, Any) -> None
        return None


def _now_seconds_since_epoch():
    # type: () -> float
    # We cannot use `time.perf_counter()` when dealing with the duration
    # of a Celery task, because the start of a Celery task and
    # the end are recorded in different processes.
    # Start happens in the Celery Beat process,
    # the end in a Celery Worker process.
    return time.time()
