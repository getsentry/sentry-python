"""
# AWS Lambda system tests

This testsuite uses boto3 to upload actual lambda functions to AWS, execute them and assert some things about the externally observed behavior. What that means for you is that those tests won't run without AWS access keys:

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

import time

from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration
import sentry_sdk
import json
from sentry_sdk.transport import HttpTransport

class TestTransport(HttpTransport):
    def _send_event(self, event):
        # Delay event output like this to test proper shutdown
        # Note that AWS Lambda truncates the log output to 4kb, so you better
        # pray that your events are smaller than that or else tests start
        # failing.
        time.sleep(1)
        print("\\nEVENT:", json.dumps(event))

def init_sdk(**extra_init_args):
    sentry_sdk.init(
        dsn="https://123abc@example.com/123",
        transport=TestTransport,
        integrations=[AwsLambdaIntegration()],
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
def run_lambda_function(tmpdir, lambda_client, request, relay_normalize):
    def inner(code, payload):
        from tests.integrations.aws_lambda.client import run_lambda_function

        runtime = request.param
        response = run_lambda_function(
            lambda_client, runtime, code, payload, request.addfinalizer
        )

        events = []

        for line in base64.b64decode(response["LogResult"]).splitlines():
            print("AWS:", line)
            if not line.startswith(b"EVENT: "):
                continue
            line = line[len(b"EVENT: ") :]
            events.append(json.loads(line.decode("utf-8")))
            relay_normalize(events[-1])

        return events, response

    return inner


def test_basic(run_lambda_function):
    events, response = run_lambda_function(
        LAMBDA_PRELUDE
        + dedent(
            """
        init_sdk()
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

    events, _response = run_lambda_function(
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
    events, _response = run_lambda_function(
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
