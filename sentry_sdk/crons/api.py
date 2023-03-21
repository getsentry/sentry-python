import uuid

from sentry_sdk import Hub
from sentry_sdk._types import TYPE_CHECKING


if TYPE_CHECKING:
    from typing import Any, Dict, Optional


def _create_check_in_event(
    monitor_slug=None,
    check_in_id=None,
    status=None,
    duration_ns=None,
    monitor_config=None,
):
    # type: (Optional[str], Optional[str], Optional[str], Optional[float], Optional[Dict[str, Any]]) -> Dict[str, Any]
    options = Hub.current.client.options if Hub.current.client else {}
    check_in_id = check_in_id or uuid.uuid4().hex  # type: str
    duration_s = duration_ns / 1e9 if duration_ns is not None else None

    check_in = {
        "type": "check_in",
        "monitor_slug": monitor_slug,
        "monitor_config": monitor_config or {},
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
    status=None,
    duration_ns=None,
    monitor_config=None,
):
    # type: (Optional[str], Optional[str], Optional[str], Optional[float], Optional[Dict[str, Any]]) -> str
    hub = Hub.current

    check_in_id = check_in_id or uuid.uuid4().hex
    check_in_event = _create_check_in_event(
        monitor_slug=monitor_slug,
        check_in_id=check_in_id,
        status=status,
        duration_ns=duration_ns,
        monitor_config=monitor_config,
    )
    hub.capture_event(check_in_event)

    return check_in_event["check_in_id"]
