from __future__ import annotations

import uuid

from sentry_sdk import Hub
from sentry_sdk._types import TYPE_CHECKING


if TYPE_CHECKING:
    from typing import Any, Dict, Optional


def _create_check_in_event(
    monitor_slug: Optional[str] = None,
    check_in_id: Optional[str] = None,
    status: Optional[str] = None,
    duration_s: Optional[float] = None,
    monitor_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    options = Hub.current.client.options if Hub.current.client else {}
    check_in_id: str = check_in_id or uuid.uuid4().hex

    check_in = {
        "type": "check_in",
        "monitor_slug": monitor_slug,
        "check_in_id": check_in_id,
        "status": status,
        "duration": duration_s,
        "environment": options.get("environment", None),
        "release": options.get("release", None),
    }

    if monitor_config:
        check_in["monitor_config"] = monitor_config

    return check_in


def capture_checkin(
    monitor_slug: Optional[str] = None,
    check_in_id: Optional[str] = None,
    status: Optional[str] = None,
    duration: Optional[float] = None,
    monitor_config: Optional[Dict[str, Any]] = None,
) -> str:
    check_in_event = _create_check_in_event(
        monitor_slug=monitor_slug,
        check_in_id=check_in_id,
        status=status,
        duration_s=duration,
        monitor_config=monitor_config,
    )

    hub = Hub.current
    hub.capture_event(check_in_event)

    return check_in_event["check_in_id"]
