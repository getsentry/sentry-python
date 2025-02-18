import boto3
import json
import pytest
import subprocess
import tempfile
import time
import yaml

from unittest import mock

from aws_cdk import App

from .utils import DummyLambdaStack, SentryTestServer, SAM_PORT


SAM_TEMPLATE_FILE = "sam.template.yaml"


@pytest.fixture(scope="session", autouse=True)
def test_environment():
    print("Setting up AWS Lambda test infrastructure")

    # Setup dummy relay to capture envelopes
    server = SentryTestServer()
    server.start()
    time.sleep(1)  # Give it a moment to start up

    # Create the SAM stack
    app = App()
    stack = DummyLambdaStack(app, "DummyLambdaStack")

    # Write template to file
    template = app.synth().get_stack_by_name("DummyLambdaStack").template
    with open(SAM_TEMPLATE_FILE, "w") as f:
        yaml.dump(template, f)

    debug_log_file = tempfile.gettempdir() + "/sentry_aws_lambda_tests_sam_debug.log"
    debug_log = open(debug_log_file, "w")
    print(f"Writing SAM debug log to: {debug_log_file}")

    # Start SAM local
    process = subprocess.Popen(
        [
            "sam",
            "local",
            "start-lambda",
            "--debug",
            "--template",
            SAM_TEMPLATE_FILE,
            "--warm-containers",
            "EAGER",
        ],
        stdout=debug_log,
        stderr=debug_log,
        text=True,  # This makes stdout/stderr return strings instead of bytes
    )

    try:
        # Wait for SAM to be ready
        DummyLambdaStack.wait_for_stack()

        def before_test():
            server.clear_envelopes()

        yield {
            "stack": stack,
            "server": server,
            "before_test": before_test,  # Add this function to the yielded dict
        }

    finally:
        print("Tearing down AWS Lambda test infrastructure")

        process.terminate()
        process.wait(timeout=5)  # Give it time to shut down gracefully

        # Force kill if still running
        if process.poll() is None:
            process.kill()


@pytest.fixture(autouse=True)
def clear_before_test(test_environment):
    test_environment["before_test"]()


@pytest.fixture
def lambda_client():
    """
    Create a boto3 client configured to use the local AWS SAM instance.
    """
    return boto3.client(
        "lambda",
        endpoint_url=f"http://127.0.0.1:{SAM_PORT}",
        aws_access_key_id="dummy",
        aws_secret_access_key="dummy",
    )


def test_basic_no_exception(lambda_client, test_environment):
    lambda_client.invoke(
        FunctionName="BasicOk",
        Payload=json.dumps({}),
    )
    envelopes = test_environment["server"].envelopes

    (transaction_event,) = envelopes

    assert transaction_event["type"] == "transaction"
    assert transaction_event["transaction"] == "BasicOk"
    assert transaction_event["sdk"]["name"] == "sentry.python.aws_lambda"
    assert transaction_event["tags"] == {"aws_region": "us-east-1"}

    assert transaction_event["extra"]["cloudwatch logs"] == {
        "log_group": mock.ANY,
        "log_stream": mock.ANY,
        "url": mock.ANY,
    }
    assert transaction_event["extra"]["lambda"] == {
        "aws_request_id": mock.ANY,
        "execution_duration_in_millis": mock.ANY,
        "function_name": "BasicOk",
        "function_version": "$LATEST",
        "invoked_function_arn": "arn:aws:lambda:us-east-1:012345678912:function:BasicOk",
        "remaining_time_in_millis": mock.ANY,
    }
    assert transaction_event["contexts"]["trace"] == {
        "op": "function.aws",
        "description": mock.ANY,
        "span_id": mock.ANY,
        "parent_span_id": mock.ANY,
        "trace_id": mock.ANY,
        "origin": "auto.function.aws_lambda",
        "data": mock.ANY,
    }


def test_basic_exception(lambda_client, test_environment):
    lambda_client.invoke(
        FunctionName="BasicException",
        Payload=json.dumps({}),
    )
    envelopes = test_environment["server"].envelopes

    # The second envelope we ignore.
    # It is the transaction that we test in test_basic_no_exception.
    (error_event, _) = envelopes

    assert error_event["level"] == "error"
    assert error_event["exception"]["values"][0]["type"] == "RuntimeError"
    assert error_event["exception"]["values"][0]["value"] == "Oh!"
    assert error_event["sdk"]["name"] == "sentry.python.aws_lambda"

    assert error_event["tags"] == {"aws_region": "us-east-1"}
    assert error_event["extra"]["cloudwatch logs"] == {
        "log_group": mock.ANY,
        "log_stream": mock.ANY,
        "url": mock.ANY,
    }
    assert error_event["extra"]["lambda"] == {
        "aws_request_id": mock.ANY,
        "execution_duration_in_millis": mock.ANY,
        "function_name": "BasicException",
        "function_version": "$LATEST",
        "invoked_function_arn": "arn:aws:lambda:us-east-1:012345678912:function:BasicException",
        "remaining_time_in_millis": mock.ANY,
    }
    assert error_event["contexts"]["trace"] == {
        "op": "function.aws",
        "description": mock.ANY,
        "span_id": mock.ANY,
        "parent_span_id": mock.ANY,
        "trace_id": mock.ANY,
        "origin": "auto.function.aws_lambda",
        "data": mock.ANY,
    }


def test_init_error(lambda_client, test_environment):
    lambda_client.invoke(
        FunctionName="InitError",
        Payload=json.dumps({}),
    )
    envelopes = test_environment["server"].envelopes

    (error_event, transaction_event) = envelopes

    assert (
        error_event["exception"]["values"][0]["value"] == "name 'func' is not defined"
    )
    assert transaction_event["transaction"] == "InitError"


def test_timeout_error(lambda_client, test_environment):
    lambda_client.invoke(
        FunctionName="TimeoutError",
        Payload=json.dumps({}),
    )
    envelopes = test_environment["server"].envelopes

    (error_event,) = envelopes

    assert error_event["level"] == "error"
    assert error_event["extra"]["lambda"]["function_name"] == "TimeoutError"

    (exception,) = error_event["exception"]["values"]
    assert not exception["mechanism"]["handled"]
    assert exception["type"] == "ServerlessTimeoutWarning"
    assert exception["value"].startswith(
        "WARNING : Function is expected to get timed out. Configured timeout duration ="
    )
    assert exception["mechanism"]["type"] == "threading"


@pytest.mark.parametrize(
    "aws_event, has_request_data, batch_size",
    [
        (b"1231", False, 1),
        (b"11.21", False, 1),
        (b'"Good dog!"', False, 1),
        (b"true", False, 1),
        (
            b"""
            [
                {"good dog": "Maisey"},
                {"good dog": "Charlie"},
                {"good dog": "Cory"},
                {"good dog": "Bodhi"}
            ]
            """,
            False,
            4,
        ),
        (
            b"""
            [
                {
                    "headers": {
                        "Host": "x1.io",
                        "X-Forwarded-Proto": "https"
                    },
                    "httpMethod": "GET",
                    "path": "/1",
                    "queryStringParameters": {
                        "done": "f"
                    },
                    "d": "D1"
                },
                {
                    "headers": {
                        "Host": "x2.io",
                        "X-Forwarded-Proto": "http"
                    },
                    "httpMethod": "POST",
                    "path": "/2",
                    "queryStringParameters": {
                        "done": "t"
                    },
                    "d": "D2"
                }
            ]
            """,
            True,
            2,
        ),
        (b"[]", False, 1),
    ],
    ids=[
        "event as integer",
        "event as float",
        "event as string",
        "event as bool",
        "event as list of dicts",
        "event as dict",
        "event as empty list",
    ],
)
def test_non_dict_event(
    lambda_client, test_environment, aws_event, has_request_data, batch_size
):
    lambda_client.invoke(
        FunctionName="BasicException",
        Payload=aws_event,
    )
    envelopes = test_environment["server"].envelopes

    (error_event, transaction_event) = envelopes

    assert transaction_event["type"] == "transaction"
    assert transaction_event["transaction"] == "BasicException"
    assert transaction_event["sdk"]["name"] == "sentry.python.aws_lambda"
    assert transaction_event["contexts"]["trace"]["status"] == "internal_error"

    assert error_event["level"] == "error"
    assert error_event["transaction"] == "BasicException"
    assert error_event["sdk"]["name"] == "sentry.python.aws_lambda"
    assert error_event["exception"]["values"][0]["type"] == "RuntimeError"
    assert error_event["exception"]["values"][0]["value"] == "Oh!"
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "aws_lambda"

    if has_request_data:
        request_data = {
            "headers": {"Host": "x1.io", "X-Forwarded-Proto": "https"},
            "method": "GET",
            "url": "https://x1.io/1",
            "query_string": {
                "done": "f",
            },
        }
    else:
        request_data = {"url": "awslambda:///BasicException"}

    assert error_event["request"] == request_data
    assert transaction_event["request"] == request_data

    if batch_size > 1:
        assert error_event["tags"]["batch_size"] == batch_size
        assert error_event["tags"]["batch_request"] is True
        assert transaction_event["tags"]["batch_size"] == batch_size
        assert transaction_event["tags"]["batch_request"] is True


def test_request_data(lambda_client, test_environment):
    payload = b"""
        {
          "resource": "/asd",
          "path": "/asd",
          "httpMethod": "GET",
          "headers": {
            "Host": "iwsz2c7uwi.execute-api.us-east-1.amazonaws.com",
            "User-Agent": "custom",
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
    """

    lambda_client.invoke(
        FunctionName="BasicOk",
        Payload=payload,
    )
    envelopes = test_environment["server"].envelopes

    (transaction_event,) = envelopes

    assert transaction_event["request"] == {
        "headers": {
            "Host": "iwsz2c7uwi.execute-api.us-east-1.amazonaws.com",
            "User-Agent": "custom",
            "X-Forwarded-Proto": "https",
        },
        "method": "GET",
        "query_string": {"bonkers": "true"},
        "url": "https://iwsz2c7uwi.execute-api.us-east-1.amazonaws.com/asd",
    }


def test_trace_continuation(lambda_client, test_environment):
    trace_id = "471a43a4192642f0b136d5159a501701"
    parent_span_id = "6e8f22c393e68f19"
    parent_sampled = 1
    sentry_trace_header = "{}-{}-{}".format(trace_id, parent_span_id, parent_sampled)

    # We simulate here AWS Api Gateway's behavior of passing HTTP headers
    # as the `headers` dict in the event passed to the Lambda function.
    payload = {
        "headers": {
            "sentry-trace": sentry_trace_header,
        }
    }

    lambda_client.invoke(
        FunctionName="BasicException",
        Payload=json.dumps(payload),
    )
    envelopes = test_environment["server"].envelopes

    (error_event, transaction_event) = envelopes

    assert (
        error_event["contexts"]["trace"]["trace_id"]
        == transaction_event["contexts"]["trace"]["trace_id"]
        == "471a43a4192642f0b136d5159a501701"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"headers": None},
        {"headers": ""},
        {"headers": {}},
        {"headers": []},  # EventBridge sends an empty list
    ],
    ids=[
        "no headers",
        "none headers",
        "empty string headers",
        "empty dict headers",
        "empty list headers",
    ],
)
def test_headers(lambda_client, test_environment, payload):
    lambda_client.invoke(
        FunctionName="BasicException",
        Payload=json.dumps(payload),
    )
    envelopes = test_environment["server"].envelopes

    (error_event, _) = envelopes

    assert error_event["level"] == "error"
    assert error_event["exception"]["values"][0]["type"] == "RuntimeError"
    assert error_event["exception"]["values"][0]["value"] == "Oh!"


def test_span_origin(lambda_client, test_environment):
    lambda_client.invoke(
        FunctionName="BasicOk",
        Payload=json.dumps({}),
    )
    envelopes = test_environment["server"].envelopes

    (transaction_event,) = envelopes

    assert (
        transaction_event["contexts"]["trace"]["origin"] == "auto.function.aws_lambda"
    )


def test_init_sentry_manually():
    # todo
    pass
