from functools import wraps
import sys

from sentry_sdk._compat import reraise
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.crons import capture_checkin
from sentry_sdk.crons.consts import MonitorStatus
from sentry_sdk.utils import nanosecond_time


if TYPE_CHECKING:
    from typing import Any, Callable, Optional


def monitor(monitor_slug=None):
    # type: (Optional[str]) -> Callable[..., Any]
    """
    Decorator to capture checkin events for a monitor.

    Usage:
    ```
    import sentry_sdk

    app = Celery()

    @app.task
    @sentry_sdk.monitor(monitor_slug='my-fancy-slug')
    def test(arg):
        print(arg)
    ```

    This does not have to be used with Celery, but if you do use it with celery,
    put the `@sentry_sdk.monitor` decorator below Celery's `@app.task` decorator.
    """

    def decorate(func):
        # type: (Callable[..., Any]) -> Callable[..., Any]
        if not monitor_slug:
            return func

        @wraps(func)
        def wrapper(*args, **kwargs):
            # type: (*Any, **Any) -> Any
            print("im wrapper monitor_slug: ", monitor_slug)
            start_timestamp = nanosecond_time()
            check_in_id = capture_checkin(
                monitor_slug=monitor_slug, status=MonitorStatus.IN_PROGRESS
            )

            try:
                result = func(*args, **kwargs)
            except Exception:
                duration_ns = nanosecond_time() - start_timestamp
                capture_checkin(
                    monitor_slug=monitor_slug,
                    check_in_id=check_in_id,
                    status=MonitorStatus.ERROR,
                    duration_ns=duration_ns,
                )
                exc_info = sys.exc_info()
                reraise(*exc_info)

            duration_ns = nanosecond_time() - start_timestamp
            capture_checkin(
                monitor_slug=monitor_slug,
                check_in_id=check_in_id,
                status=MonitorStatus.OK,
                duration_ns=duration_ns,
            )

            return result

        return wrapper

    return decorate
