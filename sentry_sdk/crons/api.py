import uuid

from sentry_sdk import Hub
from sentry_sdk._types import TYPE_CHECKING


if TYPE_CHECKING:
    from typing import Any, Dict, Optional


def _create_checkin_event(
    monitor_slug=None, check_in_id=None, status=None, duration_ns=None
):
    # type: (Optional[str], Optional[str], Optional[str], Optional[float]) -> Dict[str, Any]
    options = Hub.current.client.options if Hub.current.client else {}
    check_in_id = check_in_id or uuid.uuid4().hex  # type: str
    # convert nanosecond to millisecond
    duration_ms = int(duration_ns * 0.000001) if duration_ns is not None else None

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
        "duration": duration_ms,
        "environment": options["environment"],
        "release": options["release"],
    }

    return checkin


def capture_checkin(monitor_slug=None, check_in_id=None, status=None, duration_ns=None):
    # type: (Optional[str], Optional[str], Optional[str], Optional[float]) -> str
    hub = Hub.current

    check_in_id = check_in_id or uuid.uuid4().hex
    checkin_event = _create_checkin_event(
        monitor_slug=monitor_slug,
        check_in_id=check_in_id,
        status=status,
        duration_ns=duration_ns,
    )
    hub.capture_event(checkin_event)

    return checkin_event["check_in_id"]
