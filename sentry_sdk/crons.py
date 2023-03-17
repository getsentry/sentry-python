from functools import wraps
import sys
import uuid

from sentry_sdk import Hub
from sentry_sdk._compat import reraise
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.utils import nanosecond_time


if TYPE_CHECKING:
    from typing import Any, Callable, Dict, Optional


class MonitorStatus:
    IN_PROGRESS = "in_progress"
    OK = "ok"
    ERROR = "error"


def _create_checkin_event(
    monitor_slug=None, check_in_id=None, status=None, duration=None
):
    # type: (Optional[str], Optional[str], Optional[str], Optional[float]) -> Dict[str, Any]
    options = Hub.current.client.options if Hub.current.client else {}
    check_in_id = check_in_id or uuid.uuid4().hex  # type: str
    # convert nanosecond to millisecond
    duration = int(duration * 0.000001) if duration is not None else duration

    checkin = {
        "type": "check_in",
        "monitor_slug": monitor_slug,
        # TODO: Add schedule and schedule_type to monitor config
        # "monitor_config": {
        #     "schedule": "*/10 0 0 0 0",
        #     "schedule_type": "cron",
        # },
        "check_in_id": check_in_id,
        "status": status,
        "duration": duration,
        "environment": options["environment"],
        "release": options["release"],
    }

    return checkin


def capture_checkin(monitor_slug=None, check_in_id=None, status=None, duration=None):
    # type: (Optional[str], Optional[str], Optional[str], Optional[float]) -> str
    hub = Hub.current

    check_in_id = check_in_id or uuid.uuid4().hex
    checkin_event = _create_checkin_event(
        monitor_slug=monitor_slug,
        check_in_id=check_in_id,
        status=status,
        duration=duration,
    )
    hub.capture_event(checkin_event)

    return checkin_event["check_in_id"]


def monitor(monitor_slug=None, app=None):
    # type: (Optional[str], Any) -> Callable[..., Any]
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
            start_timestamp = nanosecond_time()
            check_in_id = capture_checkin(
                monitor_slug=monitor_slug, status=MonitorStatus.IN_PROGRESS
            )

            try:
                result = func(*args, **kwargs)
            except Exception:
                duration = nanosecond_time() - start_timestamp
                capture_checkin(
                    monitor_slug=monitor_slug,
                    check_in_id=check_in_id,
                    status=MonitorStatus.ERROR,
                    duration=duration,
                )
                exc_info = sys.exc_info()
                reraise(*exc_info)

            duration = nanosecond_time() - start_timestamp
            capture_checkin(
                monitor_slug=monitor_slug,
                check_in_id=check_in_id,
                status=MonitorStatus.OK,
                duration=duration,
            )

            return result

        return wrapper

    return decorate
