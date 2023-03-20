import uuid

from sentry_sdk import Hub
from sentry_sdk._types import TYPE_CHECKING


if TYPE_CHECKING:
    from typing import Any, Dict, Optional


def _create_checkin_event(
    monitor_slug=None,
    check_in_id=None,
    schedule=None,
    schedule_type=None,
    status=None,
    duration_ns=None,
):
    # type: (Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], Optional[float]) -> Dict[str, Any]
    options = Hub.current.client.options if Hub.current.client else {}
    check_in_id = check_in_id or uuid.uuid4().hex  # type: str
    duration_ms = int(duration_ns * 0.000001) if duration_ns is not None else None

    checkin = {
        "type": "check_in",
        "monitor_slug": monitor_slug,
        "monitor_config": {
            "schedule": schedule,
            "schedule_type": schedule_type,
        },
        "check_in_id": check_in_id,
        "status": status,
        "duration": duration_ms,
        "environment": options["environment"],
        "release": options["release"],
    }

    print("sentry_sdk.crons.api._create_checkin_event: ", checkin)

    return checkin


def capture_checkin(
    monitor_slug=None,
    check_in_id=None,
    schedule=None,
    schedule_type=None,
    status=None,
    duration_ns=None,
):
    # type: (Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], Optional[float]) -> str
    hub = Hub.current

    check_in_id = check_in_id or uuid.uuid4().hex
    checkin_event = _create_checkin_event(
        monitor_slug=monitor_slug,
        check_in_id=check_in_id,
        schedule=schedule,
        schedule_type=schedule_type,
        status=status,
        duration_ns=duration_ns,
    )
    hub.capture_event(checkin_event)

    return checkin_event["check_in_id"]
