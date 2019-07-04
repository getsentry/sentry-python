import subprocess
import sys

from sentry_sdk import Hub, capture_message
from sentry_sdk.integrations.stdlib import StdlibIntegration


def test_subprocess_basic(sentry_init, capture_events):
    sentry_init(integrations=[StdlibIntegration()], traces_sample_rate=1.0)

    with Hub.current.trace(transaction="foo", op="foo") as span:
        output = subprocess.check_output(
            [
                sys.executable,
                "-c",
                "import sentry_sdk; "
                "from sentry_sdk.integrations.stdlib import get_subprocess_traceparent_headers; "
                "sentry_sdk.init(); "
                "print(dict(get_subprocess_traceparent_headers()))",
            ]
        )

    assert span.trace_id in str(output)

    events = capture_events()

    capture_message("hi")

    event, = events

    crumb, = event["breadcrumbs"]
    assert crumb == {
        "category": "subprocess",
        "data": {},
        "timestamp": crumb["timestamp"],
        "type": "subprocess",
    }
