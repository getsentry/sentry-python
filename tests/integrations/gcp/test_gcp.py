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

def log_return_value(func):
    def inner(*args, **kwargs):
        rv = func(*args, **kwargs)

        print("\\nRETURN VALUE: {}\\n".format(json.dumps(rv)))

        return rv

    return inner

gcp_functions.worker_v1 = Mock()
gcp_functions.worker_v1.FunctionHandler = Mock()
gcp_functions.worker_v1.FunctionHandler.invoke_user_function = log_return_value(cloud_function)


import sentry_sdk
from sentry_sdk.integrations.gcp import GcpIntegration
import json
import time

from sentry_sdk.transport import HttpTransport

def event_processor(event):
    # Adding delay which would allow us to capture events.
    time.sleep(1)
    return event

def envelope_processor(envelope):
    (item,) = envelope.items
    return item.get_bytes()

class TestTransport(HttpTransport):
    def capture_envelope(self, envelope):
        envelope_item = envelope_processor(envelope)
        print("\\nENVELOPE: {}\\n".format(envelope_item.decode(\"utf-8\")))


def init_sdk(timeout_warning=False, **extra_init_args):
    sentry_sdk.init(
        dsn="https://123abc@example.com/123",
        transport=TestTransport,
        integrations=[GcpIntegration(timeout_warning=timeout_warning)],
        shutdown_timeout=10,
        # excepthook -> dedupe -> event_processor client report gets added
        # which we don't really care about for these tests
        send_client_reports=False,
        **extra_init_args
    )

"""


@pytest.fixture
def run_cloud_function():
    def inner(code, subprocess_kwargs=()):
        envelope_items = []
        return_value = None

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
            stream_data = stream.read()

            stream.close()

            for line in stream_data.splitlines():
                print("GCP:", line)
                if line.startswith("ENVELOPE: "):
                    line = line[len("ENVELOPE: ") :]
                    envelope_items.append(json.loads(line))
                elif line.startswith("RETURN VALUE: "):
                    line = line[len("RETURN VALUE: ") :]
                    return_value = json.loads(line)
                else:
                    continue

            stream.close()

        return envelope_items, return_value

    return inner


def test_handled_exception(run_cloud_function):
    envelope_items, return_value = run_cloud_function(
        dedent(
            """
        functionhandler = None
        event = {}
        def cloud_function(functionhandler, event):
            raise Exception("something went wrong")
        """
        )
        + FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk(timeout_warning=False)
        gcp_functions.worker_v1.FunctionHandler.invoke_user_function(functionhandler, event)
        """
        )
    )
    assert envelope_items[0]["level"] == "error"
    (exception,) = envelope_items[0]["exception"]["values"]

    assert exception["type"] == "Exception"
    assert exception["value"] == "something went wrong"
    assert exception["mechanism"]["type"] == "gcp"
    assert not exception["mechanism"]["handled"]


def test_unhandled_exception(run_cloud_function):
    envelope_items, _ = run_cloud_function(
        dedent(
            """
        functionhandler = None
        event = {}
        def cloud_function(functionhandler, event):
            x = 3/0
            return "3"
        """
        )
        + FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk(timeout_warning=False)
        gcp_functions.worker_v1.FunctionHandler.invoke_user_function(functionhandler, event)
        """
        )
    )
    assert envelope_items[0]["level"] == "error"
    (exception,) = envelope_items[0]["exception"]["values"]

    assert exception["type"] == "ZeroDivisionError"
    assert exception["value"] == "division by zero"
    assert exception["mechanism"]["type"] == "gcp"
    assert not exception["mechanism"]["handled"]


def test_timeout_error(run_cloud_function):
    envelope_items, _ = run_cloud_function(
        dedent(
            """
        functionhandler = None
        event = {}
        def cloud_function(functionhandler, event):
            time.sleep(10)
            return "3"
        """
        )
        + FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk(timeout_warning=True)
        gcp_functions.worker_v1.FunctionHandler.invoke_user_function(functionhandler, event)
        """
        )
    )
    assert envelope_items[0]["level"] == "error"
    (exception,) = envelope_items[0]["exception"]["values"]

    assert exception["type"] == "ServerlessTimeoutWarning"
    assert (
        exception["value"]
        == "WARNING: Function is about to time out."
    )
    assert exception["mechanism"]["type"] == "threading"
    assert not exception["mechanism"]["handled"]


def test_performance_no_error(run_cloud_function):
    envelope_items, _ = run_cloud_function(
        dedent(
            """
        functionhandler = None
        event = {}
        def cloud_function(functionhandler, event):
            return "test_string"
        """
        )
        + FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk(traces_sample_rate=1.0)
        gcp_functions.worker_v1.FunctionHandler.invoke_user_function(functionhandler, event)
        """
        )
    )

    assert envelope_items[0]["type"] == "transaction"
    assert envelope_items[0]["contexts"]["trace"]["op"] == "function.gcp"
    assert envelope_items[0]["transaction"].startswith("Google Cloud function")
    assert envelope_items[0]["transaction_info"] == {"source": "component"}
    assert envelope_items[0]["transaction"] in envelope_items[0]["request"]["url"]


def test_performance_error(run_cloud_function):
    envelope_items, _ = run_cloud_function(
        dedent(
            """
        functionhandler = None
        event = {}
        def cloud_function(functionhandler, event):
            raise Exception("something went wrong")
        """
        )
        + FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk(traces_sample_rate=1.0)
        gcp_functions.worker_v1.FunctionHandler.invoke_user_function(functionhandler, event)
        """
        )
    )

    assert envelope_items[0]["level"] == "error"
    (exception,) = envelope_items[0]["exception"]["values"]

    assert exception["type"] == "Exception"
    assert exception["value"] == "something went wrong"
    assert exception["mechanism"]["type"] == "gcp"
    assert not exception["mechanism"]["handled"]

    assert envelope_items[1]["type"] == "transaction"
    assert envelope_items[1]["contexts"]["trace"]["op"] == "function.gcp"
    assert envelope_items[1]["transaction"].startswith("Google Cloud function")
    assert envelope_items[1]["transaction"] in envelope_items[0]["request"]["url"]


def test_traces_sampler_gets_correct_values_in_sampling_context(
    run_cloud_function, DictionaryContaining  # noqa:N803
):
    # TODO: There are some decent sized hacks below. For more context, see the
    # long comment in the test of the same name in the AWS integration. The
    # situations there and here aren't identical, but they're similar enough
    # that solving one would probably solve both.

    import inspect

    _, return_value = run_cloud_function(
        dedent(
            """
            functionhandler = None

            from collections import namedtuple
            GCPEvent = namedtuple("GCPEvent", ["headers"])
            event = GCPEvent(headers={"Custom-Header": "Custom Value"})

            def cloud_function(functionhandler, event):
                # this runs after the transaction has started, which means we
                # can make assertions about traces_sampler
                try:
                    traces_sampler.assert_any_call(
                        DictionaryContaining({
                            "faas.name": "chase_into_tree",
                            "faas.region": "dogpark",
                            "gcp.function.identity": "func_ID",
                            "gcp.function.entry_point": "cloud_function",
                            "gcp.function.project": "SquirrelChasing",
                            "cloud.provider": "gcp",
                            "http.request.header.custom-header": "Custom Value",
                        })
                    )
                except AssertionError:
                    # catch the error and return it because the error itself will
                    # get swallowed by the SDK as an "internal exception"
                    return {"AssertionError raised": True}

                return {"AssertionError raised": False}
            """
        )
        + FUNCTIONS_PRELUDE
        + dedent(inspect.getsource(DictionaryContaining))
        + dedent(
            """
            os.environ["FUNCTION_NAME"] = "chase_into_tree"
            os.environ["FUNCTION_REGION"] = "dogpark"
            os.environ["GCP_PROJECT"] = "SquirrelChasing"

            def _safe_is_equal(x, y):
                # copied from conftest.py - see docstring and comments there
                try:
                    is_equal = x.__eq__(y)
                except AttributeError:
                    is_equal = NotImplemented

                if is_equal == NotImplemented:
                    return x == y

                return is_equal

            traces_sampler = Mock(return_value=True)

            init_sdk(
                traces_sampler=traces_sampler,
            )

            gcp_functions.worker_v1.FunctionHandler.invoke_user_function(functionhandler, event)
            """
        )
    )

    assert return_value["AssertionError raised"] is False


def test_error_has_new_trace_context_performance_enabled(run_cloud_function):
    """
    Check if an 'trace' context is added to errros and transactions when performance monitoring is enabled.
    """
    envelope_items, _ = run_cloud_function(
        dedent(
            """
        functionhandler = None
        event = {}
        def cloud_function(functionhandler, event):
            sentry_sdk.capture_message("hi")
            x = 3/0
            return "3"
        """
        )
        + FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk(traces_sample_rate=1.0)
        gcp_functions.worker_v1.FunctionHandler.invoke_user_function(functionhandler, event)
        """
        )
    )
    (msg_event, error_event, transaction_event) = envelope_items

    assert "trace" in msg_event["contexts"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert "trace" in error_event["contexts"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert "trace" in transaction_event["contexts"]
    assert "trace_id" in transaction_event["contexts"]["trace"]

    assert (
        msg_event["contexts"]["trace"]["trace_id"]
        == error_event["contexts"]["trace"]["trace_id"]
        == transaction_event["contexts"]["trace"]["trace_id"]
    )


def test_error_has_new_trace_context_performance_disabled(run_cloud_function):
    """
    Check if an 'trace' context is added to errros and transactions when performance monitoring is disabled.
    """
    envelope_items, _ = run_cloud_function(
        dedent(
            """
        functionhandler = None
        event = {}
        def cloud_function(functionhandler, event):
            sentry_sdk.capture_message("hi")
            x = 3/0
            return "3"
        """
        )
        + FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk(traces_sample_rate=None),  # this is the default, just added for clarity
        gcp_functions.worker_v1.FunctionHandler.invoke_user_function(functionhandler, event)
        """
        )
    )

    (msg_event, error_event) = envelope_items

    assert "trace" in msg_event["contexts"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert "trace" in error_event["contexts"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert (
        msg_event["contexts"]["trace"]["trace_id"]
        == error_event["contexts"]["trace"]["trace_id"]
    )


def test_error_has_existing_trace_context_performance_enabled(run_cloud_function):
    """
    Check if an 'trace' context is added to errros and transactions
    from the incoming 'sentry-trace' header when performance monitoring is enabled.
    """
    trace_id = "471a43a4192642f0b136d5159a501701"
    parent_span_id = "6e8f22c393e68f19"
    parent_sampled = 1
    sentry_trace_header = "{}-{}-{}".format(trace_id, parent_span_id, parent_sampled)

    envelope_items, _ = run_cloud_function(
        dedent(
            """
        functionhandler = None

        from collections import namedtuple
        GCPEvent = namedtuple("GCPEvent", ["headers"])
        event = GCPEvent(headers={"sentry-trace": "%s"})

        def cloud_function(functionhandler, event):
            sentry_sdk.capture_message("hi")
            x = 3/0
            return "3"
        """
            % sentry_trace_header
        )
        + FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk(traces_sample_rate=1.0)
        gcp_functions.worker_v1.FunctionHandler.invoke_user_function(functionhandler, event)
        """
        )
    )
    (msg_event, error_event, transaction_event) = envelope_items

    assert "trace" in msg_event["contexts"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert "trace" in error_event["contexts"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert "trace" in transaction_event["contexts"]
    assert "trace_id" in transaction_event["contexts"]["trace"]

    assert (
        msg_event["contexts"]["trace"]["trace_id"]
        == error_event["contexts"]["trace"]["trace_id"]
        == transaction_event["contexts"]["trace"]["trace_id"]
        == "471a43a4192642f0b136d5159a501701"
    )


def test_error_has_existing_trace_context_performance_disabled(run_cloud_function):
    """
    Check if an 'trace' context is added to errros and transactions
    from the incoming 'sentry-trace' header when performance monitoring is disabled.
    """
    trace_id = "471a43a4192642f0b136d5159a501701"
    parent_span_id = "6e8f22c393e68f19"
    parent_sampled = 1
    sentry_trace_header = "{}-{}-{}".format(trace_id, parent_span_id, parent_sampled)

    envelope_items, _ = run_cloud_function(
        dedent(
            """
        functionhandler = None

        from collections import namedtuple
        GCPEvent = namedtuple("GCPEvent", ["headers"])
        event = GCPEvent(headers={"sentry-trace": "%s"})

        def cloud_function(functionhandler, event):
            sentry_sdk.capture_message("hi")
            x = 3/0
            return "3"
        """
            % sentry_trace_header
        )
        + FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk(traces_sample_rate=None),  # this is the default, just added for clarity
        gcp_functions.worker_v1.FunctionHandler.invoke_user_function(functionhandler, event)
        """
        )
    )
    (msg_event, error_event) = envelope_items

    assert "trace" in msg_event["contexts"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert "trace" in error_event["contexts"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert (
        msg_event["contexts"]["trace"]["trace_id"]
        == error_event["contexts"]["trace"]["trace_id"]
        == "471a43a4192642f0b136d5159a501701"
    )


def test_span_origin(run_cloud_function):
    events, _ = run_cloud_function(
        dedent(
            """
        functionhandler = None
        event = {}
        def cloud_function(functionhandler, event):
            return "test_string"
        """
        )
        + FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk(traces_sample_rate=1.0)
        gcp_functions.worker_v1.FunctionHandler.invoke_user_function(functionhandler, event)
        """
        )
    )

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "auto.function.gcp"
