import json
import subprocess
import tempfile
import time

import boto3
import docker
import pytest
import yaml
from aws_cdk import App

from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.aws_lambda.consts import (
    CLOUD_PLATFORM,
    CLOUD_PROVIDER,
    IDENTIFIER,
    LATEST_FUNCTION_VERSION,
    ORIGIN,
)
from sentry_sdk.traces import SegmentNameSource

from .utils import SAM_PORT, LocalLambdaStack, SentryServerForTesting

DOCKER_NETWORK_NAME = "lambda-test-network"
SAM_TEMPLATE_FILE = "sam.template.yaml"
SAM_SHUTDOWN_TIMEOUT = 10


@pytest.fixture(scope="session", autouse=True)
def test_environment():
    print("[test_environment fixture] Setting up AWS Lambda test infrastructure")

    # Create a Docker network
    docker_client = docker.from_env()
    docker_client.networks.prune()
    docker_client.networks.create(DOCKER_NETWORK_NAME, driver="bridge")

    # Start Sentry server
    server = SentryServerForTesting()
    server.start()
    time.sleep(1)  # Give it a moment to start up

    # Create local AWS SAM stack
    app = App()
    stack = LocalLambdaStack(app, "LocalLambdaStack")

    # Write SAM template to file
    template = app.synth().get_stack_by_name("LocalLambdaStack").template
    with open(SAM_TEMPLATE_FILE, "w") as f:
        yaml.dump(template, f)

    # Write SAM debug log to file
    debug_log_file = tempfile.gettempdir() + "/sentry_aws_lambda_tests_sam_debug.log"
    debug_log = open(debug_log_file, "w")
    print("[test_environment fixture] Writing SAM debug log to: %s" % debug_log_file)

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
            "LAZY",  # Start each Docker container on its function's first invocation
            "--docker-network",
            DOCKER_NETWORK_NAME,
        ],
        stdout=debug_log,
        stderr=debug_log,
        text=True,  # This makes stdout/stderr return strings instead of bytes
    )

    try:
        # Wait for SAM to be ready
        LocalLambdaStack.wait_for_stack()

        def before_test():
            server.clear_envelopes()

        yield {
            "stack": stack,
            "server": server,
            "before_test": before_test,
        }

    finally:
        print("[test_environment fixture] Tearing down AWS Lambda test infrastructure")
        process.terminate()
        try:
            # Teardown is typically ~7s; escalate with kill if SAM exceeds this.
            process.wait(timeout=SAM_SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


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
        endpoint_url=f"http://127.0.0.1:{SAM_PORT}",  # noqa: E231
        aws_access_key_id="dummy",
        aws_secret_access_key="dummy",
        region_name="us-east-1",
    )


def test_init_error(lambda_client, test_environment):
    lambda_client.invoke(
        FunctionName="InitError",
        Payload=json.dumps({}),
    )
    envelopes = test_environment["server"].envelopes
    span_items = test_environment["server"].span_items

    (error_event,) = envelopes

    assert (
        error_event["exception"]["values"][0]["value"] == "name 'func' is not defined"
    )

    segment_spans = [s for s in span_items if s.get("is_segment")]
    assert len(segment_spans) == 1
    assert segment_spans[0]["name"] == "InitError"


def test_timeout_error_scope_modified(lambda_client, test_environment):
    lambda_client.invoke(
        FunctionName="TimeoutErrorScopeModified",
        Payload=json.dumps({}),
    )
    envelopes = test_environment["server"].envelopes

    (error_event,) = envelopes

    assert error_event["level"] == "error"
    assert (
        error_event["extra"]["lambda"]["function_name"] == "TimeoutErrorScopeModified"
    )

    (exception,) = error_event["exception"]["values"]
    assert not exception["mechanism"]["handled"]
    assert exception["type"] == "ServerlessTimeoutWarning"
    assert exception["value"] == "WARNING: Function is about to time out."
    assert exception["mechanism"]["type"] == "threading"

    assert error_event["tags"]["custom_tag"] == "custom_value"


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
    span_items = test_environment["server"].span_items

    (error_event,) = envelopes

    assert error_event["level"] == "error"
    assert error_event["transaction"] == "BasicException"
    assert error_event["sdk"]["name"] == "sentry.python.aws_lambda"
    assert error_event["exception"]["values"][0]["type"] == "RuntimeError"
    assert error_event["exception"]["values"][0]["value"] == "Oh!"
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == IDENTIFIER

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

    segment_spans = [s for s in span_items if s.get("is_segment")]
    assert len(segment_spans) == 1
    assert segment_spans[0]["name"] == "BasicException"
    assert segment_spans[0]["status"] == "error"


def test_request_data_with_default_data_collection(lambda_client, test_environment):
    payload = b"""
        {
          "resource": "/asd",
          "path": "/asd",
          "httpMethod": "GET",
          "headers": {
            "Host": "iwsz2c7uwi.execute-api.us-east-1.amazonaws.com",
            "User-Agent": "custom",
            "X-Forwarded-Proto": "https",
            "Authorization": "Bearer secret-token",
            "Cookie": "sessionid=secret"
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
    span_items = test_environment["server"].span_items

    segment_spans = [s for s in span_items if s.get("is_segment")]
    assert len(segment_spans) == 1
    attrs = segment_spans[0]["attributes"]

    assert _get_span_attr(attrs, SPANDATA.HTTP_REQUEST_METHOD) == "GET"
    assert _get_span_attr(attrs, SPANDATA.URL_QUERY) == "bonkers=true"


USER_INFO_PAYLOAD = b"""
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


def test_user_info_with_data_collection_user_info_on(lambda_client, test_environment):
    lambda_client.invoke(
        FunctionName="BasicOkDataCollectionUserInfoOn",
        Payload=USER_INFO_PAYLOAD,
    )
    span_items = test_environment["server"].span_items

    segment_spans = [s for s in span_items if s.get("is_segment")]
    assert len(segment_spans) == 1
    attrs = segment_spans[0]["attributes"]

    assert _get_span_attr(attrs, SPANDATA.USER_ID) == "42"


def test_request_data_with_data_collection_allowlist(lambda_client, test_environment):
    payload = b"""
        {
          "resource": "/asd",
          "path": "/asd",
          "httpMethod": "GET",
          "headers": {
            "Host": "iwsz2c7uwi.execute-api.us-east-1.amazonaws.com",
            "User-Agent": "custom",
            "X-Forwarded-Proto": "https",
            "Authorization": "Bearer secret-token",
            "Cookie": "sessionid=secret",
            "X-Allow-Me": "yes"
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
          "body": "{\\"toy\\": \\"tennisball\\"}",
          "isBase64Encoded": false
        }
    """

    lambda_client.invoke(
        FunctionName="BasicOkDataCollectionAllowlist",
        Payload=payload,
    )
    span_items = test_environment["server"].span_items

    segment_spans = [s for s in span_items if s.get("is_segment")]
    assert len(segment_spans) == 1
    attrs = segment_spans[0]["attributes"]

    assert _get_span_attr(attrs, SPANDATA.HTTP_REQUEST_METHOD) == "GET"
    # Allowlisted, non-sensitive headers pass through.
    assert _get_span_attr(attrs, f"{SPANDATA.HTTP_REQUEST_HEADER}.user-agent") == [
        "custom"
    ]
    assert _get_span_attr(attrs, f"{SPANDATA.HTTP_REQUEST_HEADER}.x-allow-me") == [
        "yes"
    ]
    # Not allowlisted -> filtered.
    assert _get_span_attr(attrs, f"{SPANDATA.HTTP_REQUEST_HEADER}.host") == [
        "[Filtered]"
    ]
    assert _get_span_attr(
        attrs, f"{SPANDATA.HTTP_REQUEST_HEADER}.x-forwarded-proto"
    ) == ["[Filtered]"]
    # Allowlisted but sensitive -> still filtered.
    assert _get_span_attr(attrs, f"{SPANDATA.HTTP_REQUEST_HEADER}.authorization") == [
        "[Filtered]"
    ]
    # Not allowlisted, and cookies are always filtered.
    assert _get_span_attr(attrs, f"{SPANDATA.HTTP_REQUEST_HEADER}.cookie") == [
        "[Filtered]"
    ]


def test_request_data_with_data_collection_denylist(lambda_client, test_environment):
    payload = b"""
        {
          "resource": "/asd",
          "path": "/asd",
          "httpMethod": "GET",
          "headers": {
            "Host": "iwsz2c7uwi.execute-api.us-east-1.amazonaws.com",
            "User-Agent": "custom",
            "X-Forwarded-Proto": "https",
            "Authorization": "Bearer secret-token",
            "Cookie": "sessionid=secret",
            "X-Custom": "keep-me"
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
          "body": "{\\"toy\\": \\"tennisball\\"}",
          "isBase64Encoded": false
        }
    """

    lambda_client.invoke(
        FunctionName="BasicOkDataCollectionDenylist",
        Payload=payload,
    )
    span_items = test_environment["server"].span_items

    segment_spans = [s for s in span_items if s.get("is_segment")]
    assert len(segment_spans) == 1
    attrs = segment_spans[0]["attributes"]

    assert _get_span_attr(attrs, SPANDATA.HTTP_REQUEST_METHOD) == "GET"
    # Not denied by any term -> pass through.
    assert _get_span_attr(attrs, f"{SPANDATA.HTTP_REQUEST_HEADER}.host") == [
        "iwsz2c7uwi.execute-api.us-east-1.amazonaws.com"
    ]
    assert _get_span_attr(attrs, f"{SPANDATA.HTTP_REQUEST_HEADER}.x-custom") == [
        "keep-me"
    ]
    # Denied by custom terms.
    assert _get_span_attr(attrs, f"{SPANDATA.HTTP_REQUEST_HEADER}.user-agent") == [
        "[Filtered]"
    ]
    assert _get_span_attr(
        attrs, f"{SPANDATA.HTTP_REQUEST_HEADER}.x-forwarded-proto"
    ) == ["[Filtered]"]
    # Denied by the built-in sensitive denylist.
    assert _get_span_attr(attrs, f"{SPANDATA.HTTP_REQUEST_HEADER}.authorization") == [
        "[Filtered]"
    ]
    # Cookies are always filtered.
    assert _get_span_attr(attrs, f"{SPANDATA.HTTP_REQUEST_HEADER}.cookie") == [
        "[Filtered]"
    ]


def test_request_data_with_data_collection_off(lambda_client, test_environment):
    payload = b"""
        {
          "resource": "/asd",
          "path": "/asd",
          "httpMethod": "GET",
          "headers": {
            "Host": "iwsz2c7uwi.execute-api.us-east-1.amazonaws.com",
            "User-Agent": "custom",
            "X-Forwarded-Proto": "https",
            "Authorization": "Bearer secret-token",
            "Cookie": "sessionid=secret"
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
          "body": "{\\"toy\\": \\"tennisball\\"}",
          "isBase64Encoded": false
        }
    """

    lambda_client.invoke(
        FunctionName="BasicOkDataCollectionOff",
        Payload=payload,
    )
    span_items = test_environment["server"].span_items

    segment_spans = [s for s in span_items if s.get("is_segment")]
    assert len(segment_spans) == 1
    attrs = segment_spans[0]["attributes"]

    assert _get_span_attr(attrs, SPANDATA.HTTP_REQUEST_METHOD) == "GET"
    # With request headers collection turned off, no header attributes are collected.
    header_attrs = [
        k for k in attrs if k.startswith(f"{SPANDATA.HTTP_REQUEST_HEADER}.")
    ]
    assert header_attrs == []


def test_url_query_params_with_data_collection_allowlist(
    lambda_client, test_environment
):
    payload = b"""
        {
          "resource": "/asd",
          "path": "/asd",
          "httpMethod": "GET",
          "headers": {
            "Host": "iwsz2c7uwi.execute-api.us-east-1.amazonaws.com",
            "X-Forwarded-Proto": "https"
          },
          "queryStringParameters": {
            "page": "2",
            "tracking": "campaign",
            "token": "secret-token"
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
        FunctionName="BasicOkDataCollectionUrlQueryAllowlist",
        Payload=payload,
    )
    span_items = test_environment["server"].span_items

    segment_spans = [s for s in span_items if s.get("is_segment")]
    assert len(segment_spans) == 1
    attrs = segment_spans[0]["attributes"]

    # Allowlisted, non-sensitive -> pass through.
    # Not allowlisted -> substituted.
    # Allowlisted but sensitive -> still filtered.
    assert (
        _get_span_attr(attrs, SPANDATA.URL_QUERY)
        == "page=2&tracking=%5BFiltered%5D&token=%5BFiltered%5D"
    )


def test_url_query_params_with_data_collection_off(lambda_client, test_environment):
    payload = b"""
        {
          "resource": "/asd",
          "path": "/asd",
          "httpMethod": "GET",
          "headers": {
            "Host": "iwsz2c7uwi.execute-api.us-east-1.amazonaws.com",
            "X-Forwarded-Proto": "https"
          },
          "queryStringParameters": {
            "page": "2",
            "tracking": "campaign"
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
        FunctionName="BasicOkDataCollectionUrlQueryOff",
        Payload=payload,
    )
    span_items = test_environment["server"].span_items

    segment_spans = [s for s in span_items if s.get("is_segment")]
    assert len(segment_spans) == 1
    attrs = segment_spans[0]["attributes"]

    # With url_query_params collection turned off, no query string is collected.
    assert SPANDATA.URL_QUERY not in attrs


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

    (error_event,) = envelopes

    assert error_event["level"] == "error"
    assert error_event["exception"]["values"][0]["type"] == "RuntimeError"
    assert error_event["exception"]["values"][0]["value"] == "Oh!"


def test_span_origin(lambda_client, test_environment):
    lambda_client.invoke(
        FunctionName="BasicOk",
        Payload=json.dumps({}),
    )
    span_items = test_environment["server"].span_items

    segment_spans = [s for s in span_items if s.get("is_segment")]
    assert len(segment_spans) == 1
    assert (
        _get_span_attr(segment_spans[0]["attributes"], SPANDATA.SENTRY_ORIGIN) == ORIGIN
    )


def test_traces_sampler_has_correct_sampling_context(lambda_client, test_environment):
    """
    Test that aws_event and aws_context are passed in the custom_sampling_context
    when using the AWS Lambda integration.
    """
    test_payload = {"test_key": "test_value"}
    response = lambda_client.invoke(
        FunctionName="TracesSampler",
        Payload=json.dumps(test_payload),
    )
    response_payload = json.loads(response["Payload"].read().decode())
    sampling_context_data = json.loads(response_payload["body"])[
        "sampling_context_data"
    ]
    assert sampling_context_data.get("aws_event_present") is True
    assert sampling_context_data.get("aws_context_present") is True
    assert sampling_context_data.get("event_data", {}).get("test_key") == "test_value"


@pytest.mark.parametrize(
    "lambda_function_name",
    ["RaiseErrorPerformanceEnabled", "RaiseErrorPerformanceDisabled"],
)
def test_error_has_new_trace_context(
    lambda_client, test_environment, lambda_function_name
):
    lambda_client.invoke(
        FunctionName=lambda_function_name,
        Payload=json.dumps({}),
    )
    envelopes = test_environment["server"].envelopes
    span_items = test_environment["server"].span_items

    (error_event,) = envelopes

    assert "trace" in error_event["contexts"]
    assert "trace_id" in error_event["contexts"]["trace"]

    if lambda_function_name == "RaiseErrorPerformanceEnabled":
        segment_spans = [s for s in span_items if s.get("is_segment")]
        assert len(segment_spans) == 1
        assert (
            error_event["contexts"]["trace"]["trace_id"] == segment_spans[0]["trace_id"]
        )


def _get_span_attr(attrs, key):
    """Extract the value from a span attribute, handling both flat and typed formats."""
    val = attrs[key]
    if isinstance(val, dict) and "value" in val:
        return val["value"]
    return val


def test_no_error(lambda_client, test_environment):
    lambda_client.invoke(
        FunctionName="BasicOkSpanStreaming",
        Payload=json.dumps({}),
    )
    envelopes = test_environment["server"].envelopes
    span_items = test_environment["server"].span_items

    assert len(envelopes) == 0

    segment_spans = [s for s in span_items if s["is_segment"]]
    assert len(segment_spans) == 1
    segment_span = segment_spans[0]

    assert segment_span["name"] == "BasicOkSpanStreaming"

    attrs = segment_span["attributes"]

    assert _get_span_attr(attrs, SPANDATA.SENTRY_OP) == OP.FUNCTION_AWS
    assert _get_span_attr(attrs, SPANDATA.SENTRY_ORIGIN) == ORIGIN
    assert _get_span_attr(attrs, SPANDATA.SENTRY_KIND) == "server"
    assert (
        _get_span_attr(attrs, SPANDATA.SENTRY_SEGMENT_NAME_SOURCE)
        == SegmentNameSource.COMPONENT
    )
    assert _get_span_attr(attrs, SPANDATA.CLOUD_PROVIDER) == CLOUD_PROVIDER
    assert _get_span_attr(attrs, SPANDATA.CLOUD_PLATFORM) == CLOUD_PLATFORM
    assert _get_span_attr(attrs, SPANDATA.CLOUD_ACCOUNT_ID) == "012345678912"
    assert _get_span_attr(attrs, SPANDATA.CLOUD_REGION) == "us-east-1"
    assert (
        _get_span_attr(attrs, SPANDATA.CLOUD_RESOURCE_ID)
        == "arn:aws:lambda:us-east-1:012345678912:function:BasicOkSpanStreaming"
    )
    assert _get_span_attr(attrs, SPANDATA.FAAS_NAME) == "BasicOkSpanStreaming"
    assert _get_span_attr(attrs, SPANDATA.FAAS_VERSION) == LATEST_FUNCTION_VERSION
    assert SPANDATA.FAAS_INVOCATION_ID in attrs
    assert (
        _get_span_attr(attrs, SPANDATA.AWS_LAMBDA_INVOKED_ARN)
        == "arn:aws:lambda:us-east-1:012345678912:function:BasicOkSpanStreaming"
    )
    assert _get_span_attr(attrs, SPANDATA.AWS_LOG_GROUP_NAMES) == [
        "aws/lambda/BasicOkSpanStreaming"
    ]
    assert _get_span_attr(attrs, SPANDATA.AWS_LOG_STREAM_NAMES) == [
        LATEST_FUNCTION_VERSION
    ]
    assert _get_span_attr(attrs, SPANDATA.FAAS_TRIGGER) == "other"
    assert SPANDATA.MESSAGING_BATCH_MESSAGE_COUNT not in attrs
    assert _get_span_attr(attrs, SPANDATA.FAAS_INSTANCE) == LATEST_FUNCTION_VERSION
    assert _get_span_attr(attrs, SPANDATA.FAAS_MAX_MEMORY) == 128 * 1024 * 1024
    assert _get_span_attr(attrs, SPANDATA.FAAS_ENTRY_POINT) == "index.handler"
    duration = _get_span_attr(attrs, SPANDATA.AWS_LAMBDA_EXECUTION_DURATION_IN_MILLIS)
    assert duration >= 0
    assert _get_span_attr(attrs, SPANDATA.FAAS_DURATION_IN_MS) == int(duration)
    assert _get_span_attr(attrs, SPANDATA.AWS_LAMBDA_REMAINING_TIME_IN_MILLIS) > 0
    assert _get_span_attr(attrs, SPANDATA.AWS_CLOUDWATCH_LOGS_URL).startswith(
        "https://console.aws.amazon.com/cloudwatch/home?region=us-east-1"
        "#logEventViewer:group=aws/lambda/BasicOkSpanStreaming;stream=$LATEST;start="
    )
    assert SPANDATA.ERROR_TYPE not in attrs


def test_error(lambda_client, test_environment):
    lambda_client.invoke(
        FunctionName="RaiseErrorSpanStreaming",
        Payload=json.dumps({}),
    )
    envelopes = test_environment["server"].envelopes
    span_items = test_environment["server"].span_items

    assert len(envelopes) == 1
    error_event = envelopes[0]
    assert error_event["level"] == "error"
    (exception,) = error_event["exception"]["values"]
    assert exception["type"] == "Exception"
    assert exception["value"] == "Oh!"
    assert exception["mechanism"]["type"] == IDENTIFIER
    assert not exception["mechanism"]["handled"]

    segment_spans = [s for s in span_items if s["is_segment"]]
    assert len(segment_spans) == 1
    segment_span = segment_spans[0]

    assert segment_span["name"] == "RaiseErrorSpanStreaming"
    assert segment_span["status"] == "error"

    attrs = segment_span["attributes"]

    assert _get_span_attr(attrs, SPANDATA.SENTRY_OP) == OP.FUNCTION_AWS
    assert _get_span_attr(attrs, SPANDATA.SENTRY_ORIGIN) == ORIGIN
    assert (
        _get_span_attr(attrs, SPANDATA.SENTRY_SEGMENT_NAME_SOURCE)
        == SegmentNameSource.COMPONENT
    )
    assert _get_span_attr(attrs, SPANDATA.CLOUD_PROVIDER) == CLOUD_PROVIDER
    assert _get_span_attr(attrs, SPANDATA.CLOUD_PLATFORM) == CLOUD_PLATFORM
    assert _get_span_attr(attrs, SPANDATA.FAAS_NAME) == "RaiseErrorSpanStreaming"
    assert (
        _get_span_attr(attrs, SPANDATA.CLOUD_RESOURCE_ID)
        == "arn:aws:lambda:us-east-1:012345678912:function:RaiseErrorSpanStreaming:5"
    )
    assert _get_span_attr(attrs, SPANDATA.FAAS_VERSION) == "5"
    assert SPANDATA.FAAS_INVOCATION_ID in attrs
    assert (
        _get_span_attr(attrs, SPANDATA.AWS_LAMBDA_INVOKED_ARN)
        == "arn:aws:lambda:us-east-1:012345678912:function:RaiseErrorSpanStreaming:5"
    )
    assert _get_span_attr(attrs, SPANDATA.AWS_LOG_GROUP_NAMES) == [
        "aws/lambda/RaiseErrorSpanStreaming"
    ]
    assert _get_span_attr(attrs, SPANDATA.AWS_LOG_STREAM_NAMES) == [
        LATEST_FUNCTION_VERSION
    ]
    assert _get_span_attr(attrs, SPANDATA.FAAS_TRIGGER) == "other"
    assert SPANDATA.MESSAGING_BATCH_MESSAGE_COUNT not in attrs
    assert _get_span_attr(attrs, SPANDATA.ERROR_TYPE) == "Exception"


def test_cold_start(lambda_client, test_environment):
    for _ in range(2):
        lambda_client.invoke(FunctionName="ColdStart", Payload=json.dumps({}))

    first, second = [
        s["attributes"]
        for s in test_environment["server"].span_items
        if s["is_segment"]
    ]
    assert _get_span_attr(first, SPANDATA.FAAS_COLDSTART) is True
    assert _get_span_attr(second, SPANDATA.FAAS_COLDSTART) is False
    # the first invocation started right after the environment was initialized.
    assert SPANDATA.AWS_LAMBDA_PROACTIVE_INITIALIZATION not in first
    # the Sentry layer runs its own handler.
    assert _get_span_attr(first, SPANDATA.FAAS_ENTRY_POINT) == "index.handler"


def _sqs_record():
    return {
        "messageId": "059f36b4-87a3-44ab-83d2-661975830a7d",
        "body": "{}",
        "eventSource": "aws:sqs",
        "eventSourceARN": "arn:aws:sqs:us-east-1:012345678912:orders",
    }


def _sns_record():
    return {
        "EventSource": "aws:sns",
        "Sns": {"TopicArn": "arn:aws:sns:us-east-1:012345678912:alerts"},
    }


def _appsync_event():
    return {
        "arguments": {"id": "1"},
        "info": {"fieldName": "getOrder", "parentTypeName": "Query"},
    }


def _eventbridge_event(source, detail_type):
    return {
        "version": "0",
        "id": "53dc4d37-cffa-4f76-80c9-8b7d4a4d2eaa",
        "detail-type": detail_type,
        "source": source,
        "account": "012345678912",
        "time": "2026-10-26T12:00:00Z",
        "region": "us-east-1",
        "resources": [],
        "detail": {},
    }


@pytest.mark.parametrize(
    "aws_event,trigger,batch_count",
    [
        pytest.param(
            {"httpMethod": "GET", "path": "/", "requestContext": {"stage": "prod"}},
            "http",
            None,
            id="api-gateway-rest",
        ),
        pytest.param(
            {
                "version": "2.0",
                "routeKey": "GET /",
                "requestContext": {"http": {"method": "GET"}},
            },
            "http",
            None,
            id="api-gateway-http",
        ),
        pytest.param(
            {"Records": [{"cf": {"request": {"uri": "/"}}}]},
            "http",
            None,
            id="cloudfront",
        ),
        pytest.param(_appsync_event(), "http", None, id="appsync"),
        pytest.param(
            [_appsync_event(), _appsync_event()], "http", None, id="appsync-batch"
        ),
        pytest.param(
            {"Records": [_sqs_record(), _sqs_record()]}, "pubsub", 2, id="sqs"
        ),
        pytest.param({"Records": [_sns_record()]}, "pubsub", 1, id="sns"),
        pytest.param(
            {"Records": [{"eventSource": "aws:kinesis"}]},
            "pubsub",
            None,
            id="kinesis",
        ),
        pytest.param(
            {
                "eventSource": "aws:kafka",
                "records": {
                    "orders-0": [{"offset": 1}, {"offset": 2}],
                    "orders-1": [{"offset": 1}],
                },
            },
            "pubsub",
            3,
            id="msk",
        ),
        pytest.param(
            {"eventSource": "SelfManagedKafka", "records": {"orders-0": [{}]}},
            "pubsub",
            1,
            id="self-managed-kafka",
        ),
        pytest.param(
            {"eventSource": "aws:mq", "messages": [{}, {}]},
            "pubsub",
            2,
            id="activemq",
        ),
        pytest.param(
            {
                "eventSource": "aws:rmq",
                "rmqMessagesByQueue": {"orders::/": [{}, {}], "alerts::/": [{}]},
            },
            "pubsub",
            3,
            id="rabbitmq",
        ),
        pytest.param(
            _eventbridge_event("aws.s3", "Object Created"),
            "pubsub",
            None,
            id="eventbridge",
        ),
        pytest.param(
            _eventbridge_event("aws.events", "Scheduled Event"),
            "timer",
            None,
            id="eventbridge-schedule",
        ),
        pytest.param(
            {"Records": [{"eventSource": "aws:s3"}]}, "datasource", None, id="s3"
        ),
        pytest.param(
            {"Records": [{"eventSource": "aws:dynamodb"}]},
            "datasource",
            None,
            id="dynamodb-streams",
        ),
        pytest.param(
            {"eventSource": "aws:docdb", "events": [{}]},
            "datasource",
            None,
            id="documentdb",
        ),
        pytest.param(
            [{"good dog": "Maisey"}, {"good dog": "Charlie"}],
            "other",
            None,
            id="list",
        ),
    ],
)
def test_trigger(lambda_client, test_environment, aws_event, trigger, batch_count):
    lambda_client.invoke(
        FunctionName="BasicOkSpanStreaming",
        Payload=json.dumps(aws_event),
    )

    (segment_span,) = [
        s for s in test_environment["server"].span_items if s["is_segment"]
    ]
    attrs = segment_span["attributes"]
    assert _get_span_attr(attrs, SPANDATA.FAAS_TRIGGER) == trigger
    if batch_count is None:
        assert SPANDATA.MESSAGING_BATCH_MESSAGE_COUNT not in attrs
    else:
        assert (
            _get_span_attr(attrs, SPANDATA.MESSAGING_BATCH_MESSAGE_COUNT) == batch_count
        )


def test_trace_continuation(lambda_client, test_environment):
    trace_id = "471a43a4192642f0b136d5159a501701"
    parent_span_id = "6e8f22c393e68f19"
    parent_sampled = 1
    sentry_trace_header = f"{trace_id}-{parent_span_id}-{parent_sampled}"

    payload = {
        "headers": {
            "sentry-trace": sentry_trace_header,
        }
    }

    lambda_client.invoke(
        FunctionName="RaiseErrorSpanStreaming",
        Payload=json.dumps(payload),
    )
    envelopes = test_environment["server"].envelopes
    span_items = test_environment["server"].span_items

    assert len(envelopes) == 1
    error_event = envelopes[0]
    assert error_event["contexts"]["trace"]["trace_id"] == trace_id

    segment_spans = [s for s in span_items if s["is_segment"]]
    assert len(segment_spans) == 1
    segment_span = segment_spans[0]
    assert segment_span["trace_id"] == trace_id
    assert segment_span["name"] == "RaiseErrorSpanStreaming"
    attrs = segment_span["attributes"]
    assert _get_span_attr(attrs, SPANDATA.SENTRY_OP) == OP.FUNCTION_AWS
    assert _get_span_attr(attrs, SPANDATA.SENTRY_ORIGIN) == ORIGIN
    assert (
        _get_span_attr(attrs, SPANDATA.SENTRY_SEGMENT_NAME_SOURCE)
        == SegmentNameSource.COMPONENT
    )
    assert _get_span_attr(attrs, SPANDATA.CLOUD_PROVIDER) == CLOUD_PROVIDER
    assert _get_span_attr(attrs, SPANDATA.CLOUD_PLATFORM) == CLOUD_PLATFORM
    assert _get_span_attr(attrs, SPANDATA.FAAS_NAME) == "RaiseErrorSpanStreaming"
    assert _get_span_attr(attrs, SPANDATA.FAAS_VERSION) == "5"
    assert SPANDATA.FAAS_INVOCATION_ID in attrs


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="client-context"),
        # `ClientContext` wins over event headers.
        pytest.param(
            {"sentry-trace": "0f4c2a5b8d9e4f1a2b3c4d5e6f7a8b9c-1a2b3c4d5e6f7a8b-1"},
            id="client-context-and-event-headers",
        ),
    ],
)
def test_trace_continuation_from_client_context(
    lambda_client, test_environment, headers
):
    payload = {
        "headers": headers,
        # `ClientContextTrace` passes it to the runtime as the invocation's `ClientContext`.
        "client_context": {
            "custom": {
                "sentry-trace": "471a43a4192642f0b136d5159a501701-6e8f22c393e68f19-1"
            }
        },
    }

    lambda_client.invoke(FunctionName="ClientContextTrace", Payload=json.dumps(payload))

    (segment_span,) = [
        s for s in test_environment["server"].span_items if s["is_segment"]
    ]
    assert segment_span["trace_id"] == "471a43a4192642f0b136d5159a501701"
    assert segment_span["parent_span_id"] == "6e8f22c393e68f19"


def test_request_attributes(lambda_client, test_environment):
    payload = {
        "headers": {
            "Content-Type": "application/json",
            "Accept": "text/html",
        },
        "httpMethod": "POST",
        "queryStringParameters": {"foo": "bar", "a-complicated-value": "a=b&c=d"},
        "path": "/test",
    }

    lambda_client.invoke(
        FunctionName="BasicOkSpanStreamingDataCollection",
        Payload=json.dumps(payload),
    )
    span_items = test_environment["server"].span_items

    segment_spans = [s for s in span_items if s["is_segment"]]
    assert len(segment_spans) == 1
    segment_span = segment_spans[0]
    attrs = segment_span["attributes"]

    assert _get_span_attr(attrs, SPANDATA.HTTP_REQUEST_METHOD) == "POST"
    assert (
        _get_span_attr(attrs, SPANDATA.URL_QUERY)
        == "foo=bar&a-complicated-value=a%3Db%26c%3Dd"
    )
    assert _get_span_attr(attrs, f"{SPANDATA.HTTP_REQUEST_HEADER}.content-type") == [
        "application/json"
    ]
    assert _get_span_attr(attrs, f"{SPANDATA.HTTP_REQUEST_HEADER}.accept") == [
        "text/html"
    ]
    assert (
        _get_span_attr(attrs, SPANDATA.FAAS_NAME)
        == "BasicOkSpanStreamingDataCollection"
    )
    assert _get_span_attr(attrs, SPANDATA.CLOUD_PROVIDER) == CLOUD_PROVIDER
    assert _get_span_attr(attrs, SPANDATA.CLOUD_PLATFORM) == CLOUD_PLATFORM
    assert (
        _get_span_attr(attrs, SPANDATA.AWS_LAMBDA_INVOKED_ARN)
        == "arn:aws:lambda:us-east-1:012345678912:function:BasicOkSpanStreamingDataCollection:prod"
    )
    assert (
        _get_span_attr(attrs, SPANDATA.CLOUD_RESOURCE_ID)
        == "arn:aws:lambda:us-east-1:012345678912:function:BasicOkSpanStreamingDataCollection:5"
    )
    assert _get_span_attr(attrs, SPANDATA.FAAS_VERSION) == "5"
    assert SPANDATA.FAAS_INVOCATION_ID in attrs
    assert _get_span_attr(attrs, SPANDATA.AWS_LOG_GROUP_NAMES) == [
        "aws/lambda/BasicOkSpanStreamingDataCollection"
    ]
    assert _get_span_attr(attrs, SPANDATA.AWS_LOG_STREAM_NAMES) == [
        LATEST_FUNCTION_VERSION
    ]


def test_url_query_params_with_data_collection(lambda_client, test_environment):
    server = test_environment["server"]
    payload = {
        "httpMethod": "GET",
        "queryStringParameters": {
            "page": "2",
            "tracking": "campaign",
            "token": "secret-token",
        },
        "path": "/test",
    }

    lambda_client.invoke(
        FunctionName="BasicOkSpanStreamingDataCollection",
        Payload=json.dumps(payload),
    )
    span_items = server.span_items

    segment_spans = [s for s in span_items if s["is_segment"]]
    assert len(segment_spans) == 1
    segment_span = segment_spans[0]
    attrs = segment_span["attributes"]

    # "page" passes through; "tracking" is denied by a custom term and "token"
    # by the built-in sensitive denylist.
    assert (
        _get_span_attr(attrs, SPANDATA.URL_QUERY)
        == "page=2&tracking=%5BFiltered%5D&token=%5BFiltered%5D"
    )


def test_user_info_with_data_collection_user_info_off(lambda_client, test_environment):
    lambda_client.invoke(
        FunctionName="BasicOkSpanStreamingDataCollectionUserInfoOff",
        Payload=USER_INFO_PAYLOAD,
    )
    span_items = test_environment["server"].span_items

    segment_spans = [s for s in span_items if s.get("is_segment")]
    assert len(segment_spans) == 1
    attrs = segment_spans[0]["attributes"]

    assert SPANDATA.USER_ID not in attrs


@pytest.mark.parametrize(
    "lambda_function_name",
    ["RaiseErrorPerformanceEnabled", "RaiseErrorPerformanceDisabled"],
)
def test_error_has_existing_trace_context(
    lambda_client, test_environment, lambda_function_name
):
    trace_id = "471a43a4192642f0b136d5159a501701"
    parent_span_id = "6e8f22c393e68f19"
    parent_sampled = 1
    sentry_trace_header = f"{trace_id}-{parent_span_id}-{parent_sampled}"

    # We simulate here AWS Api Gateway's behavior of passing HTTP headers
    # as the `headers` dict in the event passed to the Lambda function.
    payload = {
        "headers": {
            "sentry-trace": sentry_trace_header,
        }
    }

    lambda_client.invoke(
        FunctionName=lambda_function_name,
        Payload=json.dumps(payload),
    )
    envelopes = test_environment["server"].envelopes
    span_items = test_environment["server"].span_items

    (error_event,) = envelopes

    assert "trace" in error_event["contexts"]
    assert "trace_id" in error_event["contexts"]["trace"]
    assert (
        error_event["contexts"]["trace"]["trace_id"]
        == "471a43a4192642f0b136d5159a501701"
    )

    if lambda_function_name == "RaiseErrorPerformanceEnabled":
        segment_spans = [s for s in span_items if s.get("is_segment")]
        assert len(segment_spans) == 1
        assert segment_spans[0]["trace_id"] == "471a43a4192642f0b136d5159a501701"
