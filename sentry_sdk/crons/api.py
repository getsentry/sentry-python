import uuid

from sentry_sdk import Hub
from sentry_sdk._types import TYPE_CHECKING


if TYPE_CHECKING:
    from typing import Any, Dict, Optional, Tuple, Union


def _create_check_in_event(
    monitor_slug=None,
    check_in_id=None,
    schedule=None,
    schedule_type=None,
    status=None,
    duration_ns=None,
):
    # type: (Optional[str], Optional[str], Optional[Union[str, Tuple[int, str]]], Optional[str], Optional[str], Optional[float]) -> Dict[str, Any]
    options = Hub.current.client.options if Hub.current.client else {}
    check_in_id = check_in_id or uuid.uuid4().hex  # type: str
    duration_s = duration_ns / 1e9 if duration_ns is not None else None

    check_in = {
        "type": "check_in",
        "monitor_slug": monitor_slug,
        "monitor_config": {
            "schedule": schedule,
            "schedule_type": schedule_type,
        },
        "check_in_id": check_in_id,
        "status": status,
        "duration": duration_s,
        "environment": options["environment"],
        "release": options["release"],
    }

    return check_in


def capture_checkin(
    monitor_slug=None,
    check_in_id=None,
    schedule=None,
    schedule_type=None,
    status=None,
    duration_ns=None,
):
    # type: (Optional[str], Optional[str], Optional[Union[str, Tuple[int, str]]], Optional[str], Optional[str], Optional[float]) -> str
    hub = Hub.current

    check_in_id = check_in_id or uuid.uuid4().hex
    check_in_event = _create_check_in_event(
        monitor_slug=monitor_slug,
        check_in_id=check_in_id,
        schedule=schedule,
        schedule_type=schedule_type,
        status=status,
        duration_ns=duration_ns,
    )
    hub.capture_event(check_in_event)

    return check_in_event["check_in_id"]
