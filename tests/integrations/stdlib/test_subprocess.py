import os
import subprocess
import sys

import pytest

from sentry_sdk import Hub, capture_message
from sentry_sdk._compat import PY2
from sentry_sdk.integrations.stdlib import StdlibIntegration


def test_subprocess_basic(sentry_init, capture_events, monkeypatch):
    monkeypatch.setenv("FOO", "bar")

    old_environ = dict(os.environ)

    sentry_init(integrations=[StdlibIntegration()], traces_sample_rate=1.0)

    with Hub.current.span(transaction="foo", op="foo") as span:
        output = subprocess.check_output(
            [
                sys.executable,
                "-c",
                "import os; "
                "import sentry_sdk; "
                "from sentry_sdk.integrations.stdlib import get_subprocess_traceparent_headers; "
                "sentry_sdk.init(); "
                "assert os.environ['FOO'] == 'bar'; "
                "print(dict(get_subprocess_traceparent_headers()))",
            ]
        )

    assert os.environ == old_environ

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


def test_subprocess_invalid_args(sentry_init):
    sentry_init(integrations=[StdlibIntegration()])

    with pytest.raises(TypeError) as excinfo:
        subprocess.Popen()

    if PY2:
        assert "__init__() takes at least 2 arguments (1 given)" in str(excinfo.value)
    else:
        assert "missing 1 required positional argument: 'args" in str(excinfo.value)
