"""
# GCP Cloud Functions unit tests

"""

import json
import os
import os.path
import subprocess
import sys
import tempfile
from textwrap import dedent

import pytest

from sentry_sdk.traces import SpanStatus

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

class TestTransport(HttpTransport):
    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.headers.get("type") == "span":
                payload = item.payload.json
                for span_item in payload.get("items", []):
                    attrs = {k: v["value"] for k, v in span_item.get("attributes", {}).items()}
                    span_data = {k: v for k, v in span_item.items() if k != "attributes"}
                    span_data["attributes"] = attrs
                    print("\\nSPAN: {}\\n".format(json.dumps(span_data)))
            else:
                envelope_bytes = item.get_bytes()
                print("\\nENVELOPE: {}\\n".format(envelope_bytes.decode(\"utf-8\")))


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
        span_items = []
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
                [
                    "uv",
                    "build",
                    "--sdist",
                    "-p",
                    sys.executable,
                    "-o",
                    os.path.join(tmpdir, ".."),
                ],
                **subprocess_kwargs,
            )

            subprocess.check_call(
                "uv pip install -p {} ../*.tar.gz --target .".format(sys.executable),
                cwd=tmpdir,
                shell=True,
                **subprocess_kwargs,
            )

            stream = os.popen("python {}/main.py".format(tmpdir))
            stream_data = stream.read()

            stream.close()

            for line in stream_data.splitlines():
                print("GCP:", line)
                if line.startswith("ENVELOPE: "):
                    line = line[len("ENVELOPE: ") :]
                    envelope_items.append(json.loads(line))
                elif line.startswith("SPAN: "):
                    line = line[len("SPAN: ") :]
                    span_items.append(json.loads(line))
                elif line.startswith("RETURN VALUE: "):
                    line = line[len("RETURN VALUE: ") :]
                    return_value = json.loads(line)
                else:
                    continue

            stream.close()

        return envelope_items, return_value, span_items

    return inner


def test_handled_exception(run_cloud_function):
    envelope_items, return_value, _ = run_cloud_function(
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
    envelope_items, _, _ = run_cloud_function(
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
    envelope_items, _, _ = run_cloud_function(
        dedent(
            """
        functionhandler = None
        event = {}
        def cloud_function(functionhandler, event):
            sentry_sdk.set_tag("cloud_function", "true")
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
        == "WARNING : Function is expected to get timed out. Configured timeout duration = 3 seconds."
    )
    assert exception["mechanism"]["type"] == "threading"
    assert not exception["mechanism"]["handled"]

    assert envelope_items[0]["tags"]["cloud_function"] == "true"


def test_performance_no_error(run_cloud_function):
    envelope_items, _, _ = run_cloud_function(
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
    envelope_items, _, _ = run_cloud_function(
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
    run_cloud_function,
    DictionaryContaining,  # noqa:N803
):
    # TODO: There are some decent sized hacks below. For more context, see the
    # long comment in the test of the same name in the AWS integration. The
    # situations there and here aren't identical, but they're similar enough
    # that solving one would probably solve both.

    import inspect

    _, return_value, _ = run_cloud_function(
        dedent(
            """
            functionhandler = None
            event = {
                "type": "chase",
                "chasers": ["Maisey", "Charlie"],
                "num_squirrels": 2,
            }
            def cloud_function(functionhandler, event):
                # this runs after the transaction has started, which means we
                # can make assertions about traces_sampler
                try:
                    traces_sampler.assert_any_call(
                        DictionaryContaining({
                            "gcp_env": DictionaryContaining({
                                "function_name": "chase_into_tree",
                                "function_region": "dogpark",
                                "function_project": "SquirrelChasing",
                            }),
                            "gcp_event": {
                                "type": "chase",
                                "chasers": ["Maisey", "Charlie"],
                                "num_squirrels": 2,
                            },
                        })
                    )
                except AssertionError:
                    # catch the error and return it because the error itself will
                    # get swallowed by the SDK as an "internal exception"
                    return {"AssertionError raised": True,}

                return {"AssertionError raised": False,}
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
    envelope_items, _, _ = run_cloud_function(
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
    envelope_items, _, _ = run_cloud_function(
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

    envelope_items, _, _ = run_cloud_function(
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

    envelope_items, _, _ = run_cloud_function(
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
    events, _, _ = run_cloud_function(
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


def test_span_streaming_no_error(run_cloud_function):
    _, _, span_items = run_cloud_function(
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
        init_sdk(traces_sample_rate=1.0, _experiments={"trace_lifecycle": "stream"})
        gcp_functions.worker_v1.FunctionHandler.invoke_user_function(functionhandler, event)
        """
        )
    )

    assert len(span_items) == 1
    segment_span = span_items[0]

    assert segment_span["is_segment"] is True
    assert segment_span["name"] == "Google Cloud function"
    assert segment_span["attributes"]["sentry.op"] == "function.gcp"
    assert segment_span["attributes"]["sentry.origin"] == "auto.function.gcp"
    assert segment_span["attributes"]["sentry.span.source"] == "component"
    assert segment_span["attributes"]["cloud.provider"] == "gcp"
    assert segment_span["attributes"]["faas.name"] == "Google Cloud function"
    assert segment_span["attributes"]["gcp.project.id"] == "serverless_project"
    assert segment_span["attributes"]["faas.identity"] == "func_ID"
    assert segment_span["attributes"]["faas.entry_point"] == "cloud_function"


def test_span_streaming_error(run_cloud_function):
    envelope_items, _, span_items = run_cloud_function(
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
        init_sdk(traces_sample_rate=1.0, _experiments={"trace_lifecycle": "stream"})
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

    assert len(span_items) == 1
    segment_span = span_items[0]
    assert segment_span["is_segment"] is True
    assert segment_span["name"] == "Google Cloud function"
    assert segment_span["attributes"]["sentry.op"] == "function.gcp"
    assert segment_span["attributes"]["sentry.origin"] == "auto.function.gcp"
    assert segment_span["attributes"]["sentry.span.source"] == "component"
    assert segment_span["attributes"]["cloud.provider"] == "gcp"
    assert segment_span["attributes"]["faas.name"] == "Google Cloud function"
    assert segment_span["attributes"]["gcp.project.id"] == "serverless_project"
    assert segment_span["attributes"]["faas.identity"] == "func_ID"
    assert segment_span["attributes"]["faas.entry_point"] == "cloud_function"
    assert segment_span["status"] == SpanStatus.ERROR


def test_span_streaming_existing_trace_context(run_cloud_function):
    trace_id = "471a43a4192642f0b136d5159a501701"
    parent_span_id = "6e8f22c393e68f19"
    parent_sampled = 1
    sentry_trace_header = "{}-{}-{}".format(trace_id, parent_span_id, parent_sampled)

    envelope_items, _, span_items = run_cloud_function(
        dedent(
            """
        functionhandler = None

        from collections import namedtuple
        GCPEvent = namedtuple("GCPEvent", ["headers"])
        event = GCPEvent(headers={"sentry-trace": "%s"})

        def cloud_function(functionhandler, event):
            sentry_sdk.capture_message("hi")
            return "ok"
        """
            % sentry_trace_header
        )
        + FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk(traces_sample_rate=1.0, _experiments={"trace_lifecycle": "stream"})
        gcp_functions.worker_v1.FunctionHandler.invoke_user_function(functionhandler, event)
        """
        )
    )

    (msg_event,) = envelope_items
    assert msg_event["contexts"]["trace"]["trace_id"] == trace_id

    assert len(span_items) == 1
    segment_span = span_items[0]
    assert segment_span["is_segment"] is True
    assert segment_span["trace_id"] == trace_id
    assert segment_span["name"] == "Google Cloud function"
    assert segment_span["attributes"]["sentry.op"] == "function.gcp"
    assert segment_span["attributes"]["sentry.origin"] == "auto.function.gcp"
    assert segment_span["attributes"]["sentry.span.source"] == "component"
    assert segment_span["attributes"]["cloud.provider"] == "gcp"
    assert segment_span["attributes"]["faas.name"] == "Google Cloud function"
    assert segment_span["attributes"]["gcp.project.id"] == "serverless_project"
    assert segment_span["attributes"]["faas.identity"] == "func_ID"
    assert segment_span["attributes"]["faas.entry_point"] == "cloud_function"


def test_span_streaming_request_attributes(run_cloud_function):
    _, _, span_items = run_cloud_function(
        dedent(
            """
        functionhandler = None

        from collections import namedtuple
        GCPEvent = namedtuple("GCPEvent", ["headers", "method", "query_string"])
        event = GCPEvent(
            headers={"content-type": "application/json", "accept": "text/html"},
            method="POST",
            query_string=b"foo=bar",
        )

        def cloud_function(functionhandler, event):
            return "ok"
        """
        )
        + FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk(traces_sample_rate=1.0, send_default_pii=True, _experiments={"trace_lifecycle": "stream"})
        gcp_functions.worker_v1.FunctionHandler.invoke_user_function(functionhandler, event)
        """
        )
    )

    assert len(span_items) == 1
    segment_span = span_items[0]
    attrs = segment_span["attributes"]

    assert attrs["http.request.method"] == "POST"
    assert attrs["url.query"] == "foo=bar"
    assert attrs["http.request.header.content-type"] == "application/json"
    assert attrs["http.request.header.accept"] == "text/html"
    assert attrs["faas.name"] == "Google Cloud function"
    assert attrs["gcp.project.id"] == "serverless_project"
    assert attrs["faas.identity"] == "func_ID"
    assert attrs["faas.entry_point"] == "cloud_function"


def test_span_streaming_no_query_string_without_pii(run_cloud_function):
    _, _, span_items = run_cloud_function(
        dedent(
            """
        functionhandler = None

        from collections import namedtuple
        GCPEvent = namedtuple("GCPEvent", ["headers", "method", "query_string"])
        event = GCPEvent(
            headers={},
            method="GET",
            query_string=b"secret=hunter2",
        )

        def cloud_function(functionhandler, event):
            return "ok"
        """
        )
        + FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk(traces_sample_rate=1.0, send_default_pii=False, _experiments={"trace_lifecycle": "stream"})
        gcp_functions.worker_v1.FunctionHandler.invoke_user_function(functionhandler, event)
        """
        )
    )

    assert len(span_items) == 1
    segment_span = span_items[0]
    attrs = segment_span["attributes"]

    assert "url.query" not in attrs
    assert attrs["http.request.method"] == "GET"
    assert attrs["faas.name"] == "Google Cloud function"
    assert attrs["gcp.project.id"] == "serverless_project"
    assert attrs["faas.identity"] == "func_ID"
    assert attrs["faas.entry_point"] == "cloud_function"


# Sentinel: the query string (event) / ``url.query`` attribute (span) is absent.
NO_QUERY_STRING = "__NO_QUERY_STRING__"


# Each case is (send_default_pii, data_collection, expected_event, expected_span).
# ``send_default_pii`` / ``data_collection`` of None means the option is omitted.
# The two paths only diverge for the legacy (no ``data_collection``) rows: the
# event processor always records the raw query string, while the span-streaming
# path gates the ``url.query`` attribute on ``send_default_pii``.
_QUERY_STRING_DATA_COLLECTION_CASES = [
    pytest.param(
        True,
        None,
        "toy=tennisball&color=red&auth=secret",
        "toy=tennisball&color=red&auth=secret",
        id="send_default_pii_true",
    ),
    pytest.param(
        False,
        None,
        "toy=tennisball&color=red&auth=secret",
        NO_QUERY_STRING,
        id="send_default_pii_false",
    ),
    pytest.param(
        None,
        None,
        "toy=tennisball&color=red&auth=secret",
        NO_QUERY_STRING,
        id="defaults",
    ),
    # data_collection configured: query string is routed through filtering.
    # Spec defaults -> denylist: only the sensitive ``auth`` is redacted.
    pytest.param(
        None,
        {},
        "toy=tennisball&color=red&auth=[Filtered]",
        "toy=tennisball&color=red&auth=[Filtered]",
        id="data_collection_denylist_default",
    ),
    pytest.param(
        None,
        {"url_query_params": {"mode": "denylist", "terms": ["toy"]}},
        "toy=[Filtered]&color=red&auth=[Filtered]",
        "toy=[Filtered]&color=red&auth=[Filtered]",
        id="data_collection_denylist_custom_terms",
    ),
    # allowlist with only ``toy`` allowed: ``color`` is redacted even though it
    # is not sensitive, proving the redaction comes from the allowlist.
    pytest.param(
        None,
        {"url_query_params": {"mode": "allowlist", "terms": ["toy"]}},
        "toy=tennisball&color=[Filtered]&auth=[Filtered]",
        "toy=tennisball&color=[Filtered]&auth=[Filtered]",
        id="data_collection_allowlist",
    ),
    pytest.param(
        None,
        {"url_query_params": {"mode": "off"}},
        NO_QUERY_STRING,
        NO_QUERY_STRING,
        id="data_collection_off",
    ),
]


def _build_init_kwargs(send_default_pii, data_collection):
    """Render the keyword-argument string passed to ``init_sdk`` in the
    subprocess."""
    kwargs = []
    if send_default_pii is not None:
        kwargs.append("send_default_pii=%r" % send_default_pii)
    if data_collection is not None:
        kwargs.append("_experiments=%r" % {"data_collection": data_collection})

    return ", ".join(kwargs)


@pytest.mark.parametrize(
    "send_default_pii, data_collection, expected_event, expected_span",
    _QUERY_STRING_DATA_COLLECTION_CASES,
)
def test_query_string_data_collection_event_processor(
    run_cloud_function,
    send_default_pii,
    data_collection,
    expected_event,
    expected_span,
):
    init_kwargs = _build_init_kwargs(send_default_pii, data_collection)
    envelope_items, _, _ = run_cloud_function(
        dedent(
            """
        functionhandler = None

        from collections import namedtuple
        GCPEvent = namedtuple("GCPEvent", ["headers", "method", "query_string"])
        event = GCPEvent(
            headers={},
            method="GET",
            query_string=b"toy=tennisball&color=red&auth=secret",
        )

        def cloud_function(functionhandler, event):
            raise Exception("something went wrong")
        """
        )
        + FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk(%s)
        gcp_functions.worker_v1.FunctionHandler.invoke_user_function(functionhandler, event)
        """
            % init_kwargs
        )
    )

    request = envelope_items[0]["request"]
    if expected_event == NO_QUERY_STRING:
        assert "query_string" not in request
    else:
        assert request["query_string"] == expected_event


@pytest.mark.parametrize(
    "send_default_pii, data_collection, expected_event, expected_span",
    _QUERY_STRING_DATA_COLLECTION_CASES,
)
def test_query_string_data_collection_span_streaming(
    run_cloud_function,
    send_default_pii,
    data_collection,
    expected_event,
    expected_span,
):
    init_kwargs = _build_init_kwargs(send_default_pii, data_collection)
    _, _, span_items = run_cloud_function(
        dedent(
            """
        functionhandler = None

        from collections import namedtuple
        GCPEvent = namedtuple("GCPEvent", ["headers", "method", "query_string"])
        event = GCPEvent(
            headers={},
            method="GET",
            query_string=b"toy=tennisball&color=red&auth=secret",
        )

        def cloud_function(functionhandler, event):
            return "ok"
        """
        )
        + FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk(traces_sample_rate=1.0, trace_lifecycle="stream", %s)
        gcp_functions.worker_v1.FunctionHandler.invoke_user_function(functionhandler, event)
        """
            % init_kwargs
        )
    )

    assert len(span_items) == 1
    attrs = span_items[0]["attributes"]
    if expected_span == NO_QUERY_STRING:
        assert "url.query" not in attrs
    else:
        assert attrs["url.query"] == expected_span
