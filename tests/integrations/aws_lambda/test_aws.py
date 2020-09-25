"""
# AWS Lambda system tests

This testsuite uses boto3 to upload actual lambda functions to AWS, execute
them and assert some things about the externally observed behavior. What that
means for you is that those tests won't run without AWS access keys:

    export SENTRY_PYTHON_TEST_AWS_ACCESS_KEY_ID=..
    export SENTRY_PYTHON_TEST_AWS_SECRET_ACCESS_KEY=...
    export SENTRY_PYTHON_TEST_AWS_IAM_ROLE="arn:aws:iam::920901907255:role/service-role/lambda"

If you need to debug a new runtime, use this REPL to figure things out:

    pip3 install click
    python3 tests/integrations/aws_lambda/client.py --runtime=python4.0
"""
import base64
import json
import os
import re
from textwrap import dedent

import pytest

boto3 = pytest.importorskip("boto3")

LAMBDA_PRELUDE = """
from __future__ import print_function

from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration
import sentry_sdk
import json
import time

from sentry_sdk.transport import HttpTransport

def event_processor(event):
    # AWS Lambda truncates the log output to 4kb. If you only need a
    # subsection of the event, override this function in your test
    # to print less to logs.
    return event

def envelope_processor(envelope):
    (item,) = envelope.items
    envelope_json = json.loads(item.get_bytes())

    envelope_data = {}
    envelope_data[\"contexts\"] = {}
    envelope_data[\"type\"] = envelope_json[\"type\"]
    envelope_data[\"transaction\"] = envelope_json[\"transaction\"]
    envelope_data[\"contexts\"][\"trace\"] = envelope_json[\"contexts\"][\"trace\"]
    envelope_data[\"request\"] = envelope_json[\"request\"]

    return envelope_data

class TestTransport(HttpTransport):
    def _send_event(self, event):
        event = event_processor(event)
        # Writing a single string to stdout holds the GIL (seems like) and
        # therefore cannot be interleaved with other threads. This is why we
        # explicitly add a newline at the end even though `print` would provide
        # us one.
        print("\\nEVENT: {}\\n".format(json.dumps(event)))

    def _send_envelope(self, envelope):
        envelope = envelope_processor(envelope)
        print("\\nENVELOPE: {}\\n".format(json.dumps(envelope)))

def init_sdk(timeout_warning=False, **extra_init_args):
    sentry_sdk.init(
        dsn="https://123abc@example.com/123",
        transport=TestTransport,
        integrations=[AwsLambdaIntegration(timeout_warning=timeout_warning)],
        shutdown_timeout=10,
        **extra_init_args
    )
"""


@pytest.fixture
def lambda_client():
    if "SENTRY_PYTHON_TEST_AWS_ACCESS_KEY_ID" not in os.environ:
        pytest.skip("AWS environ vars not set")

    from tests.integrations.aws_lambda.client import get_boto_client

    return get_boto_client()


@pytest.fixture(params=["python3.6", "python3.7", "python3.8", "python2.7"])
def lambda_runtime(request):
    return request.param


@pytest.fixture
def run_lambda_function(request, lambda_client, lambda_runtime):
    def inner(code, payload, timeout=30, syntax_check=True):
        from tests.integrations.aws_lambda.client import run_lambda_function

        response = run_lambda_function(
            client=lambda_client,
            runtime=lambda_runtime,
            code=code,
            payload=payload,
            add_finalizer=request.addfinalizer,
            timeout=timeout,
            syntax_check=syntax_check,
        )

        events = []
        envelopes = []

        for line in base64.b64decode(response["LogResult"]).splitlines():
            print("AWS:", line)
            if line.startswith(b"EVENT: "):
                line = line[len(b"EVENT: ") :]
                events.append(json.loads(line.decode("utf-8")))
            elif line.startswith(b"ENVELOPE: "):
                line = line[len(b"ENVELOPE: ") :]
                envelopes.append(json.loads(line.decode("utf-8")))
            else:
                continue

        return envelopes, events, response

    return inner


def test_basic(run_lambda_function):
    envelopes, events, response = run_lambda_function(
        LAMBDA_PRELUDE
        + dedent(
            """
        init_sdk()

        def event_processor(event):
            # Delay event output like this to test proper shutdown
            time.sleep(1)
            return event

        def test_handler(event, context):
            raise Exception("something went wrong")
        """
        ),
        b'{"foo": "bar"}',
    )

    assert response["FunctionError"] == "Unhandled"

    (event,) = events
    assert event["level"] == "error"
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "Exception"
    assert exception["value"] == "something went wrong"

    (frame1,) = exception["stacktrace"]["frames"]
    assert frame1["filename"] == "test_lambda.py"
    assert frame1["abs_path"] == "/var/task/test_lambda.py"
    assert frame1["function"] == "test_handler"

    assert frame1["in_app"] is True

    assert exception["mechanism"] == {"type": "aws_lambda", "handled": False}

    assert event["extra"]["lambda"]["function_name"].startswith("test_function_")

    logs_url = event["extra"]["cloudwatch logs"]["url"]
    assert logs_url.startswith("https://console.aws.amazon.com/cloudwatch/home?region=")
    assert not re.search("(=;|=$)", logs_url)
    assert event["extra"]["cloudwatch logs"]["log_group"].startswith(
        "/aws/lambda/test_function_"
    )

    log_stream_re = "^[0-9]{4}/[0-9]{2}/[0-9]{2}/\\[[^\\]]+][a-f0-9]+$"
    log_stream = event["extra"]["cloudwatch logs"]["log_stream"]

    assert re.match(log_stream_re, log_stream)


def test_initialization_order(run_lambda_function):
    """Zappa lazily imports our code, so by the time we monkeypatch the handler
    as seen by AWS already runs. At this point at least draining the queue
    should work."""

    envelopes, events, _response = run_lambda_function(
        LAMBDA_PRELUDE
        + dedent(
            """
            def test_handler(event, context):
                init_sdk()
                sentry_sdk.capture_exception(Exception("something went wrong"))
        """
        ),
        b'{"foo": "bar"}',
    )

    (event,) = events
    assert event["level"] == "error"
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "Exception"
    assert exception["value"] == "something went wrong"


def test_request_data(run_lambda_function):
    envelopes, events, _response = run_lambda_function(
        LAMBDA_PRELUDE
        + dedent(
            """
        init_sdk()
        def test_handler(event, context):
            sentry_sdk.capture_message("hi")
            return "ok"
        """
        ),
        payload=b"""
        {
          "resource": "/asd",
          "path": "/asd",
          "httpMethod": "GET",
          "headers": {
            "Host": "iwsz2c7uwi.execute-api.us-east-1.amazonaws.com",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.13; rv:62.0) Gecko/20100101 Firefox/62.0",
            "X-Forwarded-Proto": "https"
          },
          "queryStringParameters": {
            "bonkers": "true"
          },
          "pathParameters": null,
          "stageVariables": null,
          "requestContext": {
            "identity": {
              "sourceIp": "213.47.147.207",
              "userArn": "42"
            }
          },
          "body": null,
          "isBase64Encoded": false
        }
        """,
    )

    (event,) = events

    assert event["request"] == {
        "headers": {
            "Host": "iwsz2c7uwi.execute-api.us-east-1.amazonaws.com",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.13; rv:62.0) Gecko/20100101 Firefox/62.0",
            "X-Forwarded-Proto": "https",
        },
        "method": "GET",
        "query_string": {"bonkers": "true"},
        "url": "https://iwsz2c7uwi.execute-api.us-east-1.amazonaws.com/asd",
    }


def test_init_error(run_lambda_function, lambda_runtime):
    if lambda_runtime == "python2.7":
        pytest.skip("initialization error not supported on Python 2.7")

    envelopes, events, response = run_lambda_function(
        LAMBDA_PRELUDE
        + (
            "def event_processor(event):\n"
            '    return event["exception"]["values"][0]["value"]\n'
            "init_sdk()\n"
            "func()"
        ),
        b'{"foo": "bar"}',
        syntax_check=False,
    )

    (event,) = events
    assert "name 'func' is not defined" in event


def test_timeout_error(run_lambda_function):
    envelopes, events, response = run_lambda_function(
        LAMBDA_PRELUDE
        + dedent(
            """
        init_sdk(timeout_warning=True)

        def test_handler(event, context):
            time.sleep(10)
            return 0
        """
        ),
        b'{"foo": "bar"}',
        timeout=3,
    )

    (event,) = events
    assert event["level"] == "error"
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ServerlessTimeoutWarning"
    assert exception["value"] in (
        "WARNING : Function is expected to get timed out. Configured timeout duration = 4 seconds.",
        "WARNING : Function is expected to get timed out. Configured timeout duration = 3 seconds.",
    )

    assert exception["mechanism"] == {"type": "threading", "handled": False}

    assert event["extra"]["lambda"]["function_name"].startswith("test_function_")

    logs_url = event["extra"]["cloudwatch logs"]["url"]
    assert logs_url.startswith("https://console.aws.amazon.com/cloudwatch/home?region=")
    assert not re.search("(=;|=$)", logs_url)
    assert event["extra"]["cloudwatch logs"]["log_group"].startswith(
        "/aws/lambda/test_function_"
    )

    log_stream_re = "^[0-9]{4}/[0-9]{2}/[0-9]{2}/\\[[^\\]]+][a-f0-9]+$"
    log_stream = event["extra"]["cloudwatch logs"]["log_stream"]

    assert re.match(log_stream_re, log_stream)


def test_performance_no_error(run_lambda_function):
    envelopes, events, response = run_lambda_function(
        LAMBDA_PRELUDE
        + dedent(
            """
        init_sdk(traces_sample_rate=1.0)

        def test_handler(event, context):
            return "test_string"
        """
        ),
        b'{"foo": "bar"}',
    )

    (envelope,) = envelopes
    assert envelope["type"] == "transaction"
    assert envelope["contexts"]["trace"]["op"] == "serverless.function"
    assert envelope["transaction"].startswith("test_function_")
    assert envelope["transaction"] in envelope["request"]["url"]


def test_performance_error(run_lambda_function):
    envelopes, events, response = run_lambda_function(
        LAMBDA_PRELUDE
        + dedent(
            """
        init_sdk(traces_sample_rate=1.0)

        def test_handler(event, context):
            raise Exception("something went wrong")
        """
        ),
        b'{"foo": "bar"}',
    )

    (event,) = events
    assert event["level"] == "error"
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "Exception"
    assert exception["value"] == "something went wrong"

    (envelope,) = envelopes

    assert envelope["type"] == "transaction"
    assert envelope["contexts"]["trace"]["op"] == "serverless.function"
    assert envelope["transaction"].startswith("test_function_")
    assert envelope["transaction"] in envelope["request"]["url"]
