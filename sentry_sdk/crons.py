from functools import wraps
import sys
import uuid

from sentry_sdk import Hub
from sentry_sdk._compat import reraise


class MonitorStatus:
    IN_PROGRESS = "in_progress"
    OK = "ok"
    ERROR = "error"


def _create_checkin_event(monitor_id=None, status=None):
    checkin = {
        "monitor_id": monitor_id,
        "check_in_id": uuid.uuid4().hex,
        "status": status,
    }

    return checkin


def capture_checkin(monitor_id=None, status=None):
    hub = Hub.current
    checkin_event = _create_checkin_event(monitor_id, status)
    hub.capture_event(checkin_event)


def monitor(monitor_id=None):
    def decorate(func):
        if not monitor_id:
            return func

        @wraps(func)
        def wrapper(*args, **kwargs):
            capture_checkin(monitor_id=monitor_id, status=MonitorStatus.IN_PROGRESS)

            try:
                result = func(*args, **kwargs)
            except Exception:
                capture_checkin(monitor_id=monitor_id, status=MonitorStatus.ERROR)
                exc_info = sys.exc_info()
                reraise(*exc_info)

            capture_checkin(monitor_id=monitor_id, status=MonitorStatus.OK)
            return result

        return wrapper

    return decorate
