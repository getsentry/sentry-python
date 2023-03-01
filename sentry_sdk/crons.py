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
    event = {
        "type": "check_in",
        "monitor_id": monitor_id,
        "check_in_id": uuid.uuid4().hex,
        "status": status,
    }

    return event


def _capture_monitor_event(monitor_id=None, status=None):
    hub = Hub.current
    monitor_event = _create_checkin_event(monitor_id, status)
    hub.capture_event(monitor_event)

    # self.capture_envelope(Envelope(items=[client_report]))


def monitor(monitor_id=None):
    def decorate(func):
        if not monitor_id:
            return func

        @wraps(func)
        def wrapper(*args, **kwargs):
            _capture_monitor_event(
                monitor_id=monitor_id, status=MonitorStatus.IN_PROGRESS
            )

            try:
                result = func(*args, **kwargs)
            except Exception:
                _capture_monitor_event(
                    monitor_id=monitor_id, status=MonitorStatus.ERROR
                )
                exc_info = sys.exc_info()
                reraise(*exc_info)

            _capture_monitor_event(monitor_id=monitor_id, status=MonitorStatus.OK)
            return result

        return wrapper

    return decorate
