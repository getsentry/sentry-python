"""
# GCP Cloud Functions unit tests

"""
import json
from textwrap import dedent
import tempfile
import sys
import subprocess

import pytest
import os.path
import os

pytest.importorskip("tempfile.TemporaryDirectory")


FUNCTIONS_PRELUDE = """
from unittest.mock import Mock
import __main__ as gcp_functions
import os

# Initializing all the necessary environment variables
os.environ["FUNCTION_TIMEOUT_SEC"] = "3"
os.environ["FUNCTION_NAME"] = "Google Cloud function"
os.environ["ENTRY_POINT"] = "cloud_function"
os.environ["FUNCTION_IDENTITY"] = "func_ID"
os.environ["FUNCTION_REGION"] = "us-central1"
os.environ["GCP_PROJECT"] = "serverless_project"

gcp_functions.worker_v1 = Mock()
gcp_functions.worker_v1.FunctionHandler = Mock()
gcp_functions.worker_v1.FunctionHandler.invoke_user_function = cloud_function
function = gcp_functions.worker_v1.FunctionHandler.invoke_user_function


import sentry_sdk
from sentry_sdk.integrations.gcp import GcpIntegration
import json
import time

from sentry_sdk.transport import HttpTransport

def event_processor(event):
    # Adding delay which would allow us to capture events.
    time.sleep(1)
    return event

class TestTransport(HttpTransport):
    def _send_event(self, event):
        event = event_processor(event)
        # Writing a single string to stdout holds the GIL (seems like) and
        # therefore cannot be interleaved with other threads. This is why we
        # explicitly add a newline at the end even though `print` would provide
        # us one.
        print("EVENTS: {}".format(json.dumps(event)))

def init_sdk(timeout_warning=False, **extra_init_args):
    sentry_sdk.init(
        dsn="https://123abc@example.com/123",
        transport=TestTransport,
        integrations=[GcpIntegration(timeout_warning=timeout_warning)],
        shutdown_timeout=10,
        **extra_init_args
    )

"""


@pytest.fixture
def run_cloud_function():
    def inner(code, subprocess_kwargs=()):

        event = []

        # STEP : Create a zip of cloud function

        subprocess_kwargs = dict(subprocess_kwargs)

        with tempfile.TemporaryDirectory() as tmpdir:
            main_py = os.path.join(tmpdir, "main.py")
            with open(main_py, "w") as f:
                f.write(code)

            setup_cfg = os.path.join(tmpdir, "setup.cfg")

            with open(setup_cfg, "w") as f:
                f.write("[install]\nprefix=")

            subprocess.check_call(
                [sys.executable, "setup.py", "sdist", "-d", os.path.join(tmpdir, "..")],
                **subprocess_kwargs
            )

            subprocess.check_call(
                "pip install ../*.tar.gz -t .",
                cwd=tmpdir,
                shell=True,
                **subprocess_kwargs
            )

            stream = os.popen("python {}/main.py".format(tmpdir))
            event = stream.read()
            event = json.loads(event[len("EVENT: ") :])

        return event

    return inner


def test_handled_exception(run_cloud_function):
    event = run_cloud_function(
        dedent(
            """
        def cloud_function():
            raise Exception("something went wrong")
        """
        )
        + FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk(timeout_warning=False)
        gcp_functions.worker_v1.FunctionHandler.invoke_user_function()
        """
        )
    )
    assert event["level"] == "error"
    (exception,) = event["exception"]["values"]

    assert exception["type"] == "Exception"
    assert exception["value"] == "something went wrong"
    assert exception["mechanism"] == {"type": "gcp", "handled": False}


def test_unhandled_exception(run_cloud_function):
    event = run_cloud_function(
        dedent(
            """
        def cloud_function():
            x = 3/0
            return "3"
        """
        )
        + FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk(timeout_warning=False)
        gcp_functions.worker_v1.FunctionHandler.invoke_user_function()
        """
        )
    )
    assert event["level"] == "error"
    (exception,) = event["exception"]["values"]

    assert exception["type"] == "ZeroDivisionError"
    assert exception["value"] == "division by zero"
    assert exception["mechanism"] == {"type": "gcp", "handled": False}


def test_timeout_error(run_cloud_function):
    event = run_cloud_function(
        dedent(
            """
        def cloud_function():
            time.sleep(10)
            return "3"
        """
        )
        + FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk(timeout_warning=True)
        gcp_functions.worker_v1.FunctionHandler.invoke_user_function()
        """
        )
    )
    assert event["level"] == "error"
    (exception,) = event["exception"]["values"]

    assert exception["type"] == "ServerlessTimeoutWarning"
    assert (
        exception["value"]
        == "WARNING : Function is expected to get timed out. Configured timeout duration = 3 seconds."
    )
    assert exception["mechanism"] == {"type": "threading", "handled": False}
