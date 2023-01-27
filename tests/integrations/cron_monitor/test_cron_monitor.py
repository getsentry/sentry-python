import uuid
import os

from sentry_sdk import capture_message

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


def test_basic(sentry_init, capture_events):
    monitor_id = uuid.uuid4().hex
    with mock.patch.dict(
        os.environ, {**os.environ, "SENTRY_MONITOR_ID": monitor_id}, clear=True
    ):
        from sentry_sdk.integrations.cron_monitor import CronMonitorIntegration

        sentry_init(integrations=[CronMonitorIntegration()])
        events = capture_events()
        capture_message("hi")

    (event,) = events
    assert event["contexts"]["monitor"] == {"id": monitor_id}
