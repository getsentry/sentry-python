import json
import subprocess
import tempfile
import time
from unittest import mock

import boto3
import docker
import pytest
import yaml
from aws_cdk import App

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
def lambda_client(test_environment):
    """
    Create a boto3 client configured to use the local AWS SAM instance.

    The returned client's `invoke` waits after each invocation until envelope
    delivery to the test server has settled (no new envelopes for a short
    quiet period). The Lambda flushes Sentry events asynchronously, so reading
    the server's envelopes immediately after `invoke` returns is racy,
    especially when the Docker network path is slow (e.g. colima on macOS).
    """
    client = boto3.client(
        "lambda",
        endpoint_url=f"http://127.0.0.1:{SAM_PORT}",  # noqa: E231
        aws_access_key_id="dummy",
        aws_secret_access_key="dummy",
        region_name="us-east-1",
    )

    server = test_environment["server"]
    real_invoke = client.invoke

    def invoke_and_wait(**kwargs):
        before = len(server.envelopes) + len(server.span_items)
        result = real_invoke(**kwargs)
        deadline = time.time() + 30
        last_count = before
        stable_polls = 0
        while time.time() < deadline:
            count = len(server.envelopes) + len(server.span_items)
            if count > before and count == last_count:
                stable_polls += 1
                if stable_polls >= 3:  # ~1.5s without new envelopes
                    break
            else:
                stable_polls = 0
                last_count = count
            time.sleep(0.5)
        return result

    client.invoke = invoke_and_wait
    return client


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

    # Request data with send_default_pii=False: sensitive headers are
    # filtered out of the transaction's request data.
    test_environment["before_test"]()
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
    envelopes = test_environment["server"].envelopes

    (transaction_event,) = envelopes

    assert transaction_event["request"] == {
        "headers": {
            "Host": "iwsz2c7uwi.execute-api.us-east-1.amazonaws.com",
            "User-Agent": "custom",
            # X-Forwarded-Proto is not sensitive and passes through.
            "X-Forwarded-Proto": "https",
            # With send_default_pii=False, _filter_headers substitutes the
            # SENSITIVE_HEADERS (Authorization, Cookie); the EventScrubber
            # also scrubs them. Both end up as "[Filtered]".
            "Authorization": "[Filtered]",
            "Cookie": "[Filtered]",
        },
        "method": "GET",
        "query_string": {"bonkers": "true"},
        "url": "https://iwsz2c7uwi.execute-api.us-east-1.amazonaws.com/asd",
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

    # Non-dict headers are coerced to {} (None and "" hit the same branch).
    # EventBridge sends an empty list.
    test_environment["before_test"]()
    lambda_client.invoke(
        FunctionName="BasicException",
        Payload=json.dumps({"headers": []}),
    )
    envelopes = test_environment["server"].envelopes

    (error_event, _) = envelopes

    assert error_event["level"] == "error"
    assert error_event["exception"]["values"][0]["type"] == "RuntimeError"
    assert error_event["exception"]["values"][0]["value"] == "Oh!"


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
    assert exception["value"].startswith(
        "WARNING : Function is expected to get timed out. Configured timeout duration ="
    )
    assert exception["mechanism"]["type"] == "threading"

    assert error_event["tags"]["custom_tag"] == "custom_value"


@pytest.mark.parametrize(
    "aws_event, has_request_data, batch_size",
    [
        (b"1231", False, 1),
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
    ],
    ids=[
        "event as integer",
        "event as list of dicts",
        "event as dict",
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


def test_user_info_with_data_collection(lambda_client, test_environment):
    lambda_client.invoke(
        FunctionName="BasicOkDataCollectionUserInfoOn",
        Payload=USER_INFO_PAYLOAD,
    )
    envelopes = test_environment["server"].envelopes

    (transaction_event,) = envelopes

    assert transaction_event["user"] == {
        "id": "42",
        "ip_address": "213.47.147.207",
    }

    test_environment["before_test"]()
    lambda_client.invoke(
        FunctionName="BasicOkDataCollectionUserInfoOff",
        Payload=USER_INFO_PAYLOAD,
    )
    envelopes = test_environment["server"].envelopes

    (transaction_event,) = envelopes

    assert "user" not in transaction_event


def _request_data_payload(extra_headers=None):
    headers = {
        "Host": "iwsz2c7uwi.execute-api.us-east-1.amazonaws.com",
        "User-Agent": "custom",
        "X-Forwarded-Proto": "https",
        "Authorization": "Bearer secret-token",
        "Cookie": "sessionid=secret",
    }
    headers.update(extra_headers or {})
    return json.dumps(
        {
            "resource": "/asd",
            "path": "/asd",
            "httpMethod": "GET",
            "headers": headers,
            "queryStringParameters": {"bonkers": "true"},
            "pathParameters": None,
            "stageVariables": None,
            "requestContext": {
                "identity": {"sourceIp": "213.47.147.207", "userArn": "42"}
            },
            "body": None,
            "isBase64Encoded": False,
        }
    ).encode()


def test_request_data_with_data_collection(lambda_client, test_environment):
    lambda_client.invoke(
        FunctionName="BasicOkDataCollectionAllowlist",
        Payload=_request_data_payload({"X-Allow-Me": "yes"}),
    )
    envelopes = test_environment["server"].envelopes

    (transaction_event,) = envelopes

    assert transaction_event["request"] == {
        "headers": {
            # Allowlisted, non-sensitive headers pass through.
            "User-Agent": "custom",
            "X-Allow-Me": "yes",
            # Not allowlisted -> substituted.
            "Host": "[Filtered]",
            "X-Forwarded-Proto": "[Filtered]",
            # Allowlisted but sensitive -> still filtered; an allowlist entry
            # cannot override the built-in sensitive denylist.
            "Authorization": "[Filtered]",
            # Not allowlisted, and cookies are always substituted.
            "Cookie": "[Filtered]",
        },
        "method": "GET",
        "query_string": {"bonkers": "true"},
        "url": "https://iwsz2c7uwi.execute-api.us-east-1.amazonaws.com/asd",
    }

    test_environment["before_test"]()
    lambda_client.invoke(
        FunctionName="BasicOkDataCollectionDenylist",
        Payload=_request_data_payload({"X-Custom": "keep-me"}),
    )
    envelopes = test_environment["server"].envelopes

    (transaction_event,) = envelopes

    assert transaction_event["request"] == {
        "headers": {
            "Host": "iwsz2c7uwi.execute-api.us-east-1.amazonaws.com",
            "X-Custom": "keep-me",
            # Denied by custom terms.
            "User-Agent": "[Filtered]",
            "X-Forwarded-Proto": "[Filtered]",
            # Denied by the built-in sensitive denylist.
            "Authorization": "[Filtered]",
            # Cookies are always substituted.
            "Cookie": "[Filtered]",
        },
        "method": "GET",
        "query_string": {"bonkers": "true"},
        "url": "https://iwsz2c7uwi.execute-api.us-east-1.amazonaws.com/asd",
    }

    test_environment["before_test"]()
    lambda_client.invoke(
        FunctionName="BasicOkDataCollectionOff",
        Payload=_request_data_payload(),
    )
    envelopes = test_environment["server"].envelopes

    (transaction_event,) = envelopes

    assert transaction_event["request"] == {
        # With request headers collection turned off, no headers are collected.
        "headers": {},
        "method": "GET",
        "query_string": {"bonkers": "true"},
        "url": "https://iwsz2c7uwi.execute-api.us-east-1.amazonaws.com/asd",
    }

    # Legacy send_default_pii=True arm (no data_collection config):
    # _filter_headers passes headers through untouched; Authorization and
    # Cookie are still scrubbed by the always-on EventScrubber.
    test_environment["before_test"]()
    lambda_client.invoke(
        FunctionName="BasicOkSendDefaultPii",
        Payload=_request_data_payload(),
    )
    envelopes = test_environment["server"].envelopes

    (transaction_event,) = envelopes

    assert transaction_event["request"] == {
        "headers": {
            "Host": "iwsz2c7uwi.execute-api.us-east-1.amazonaws.com",
            "User-Agent": "custom",
            "X-Forwarded-Proto": "https",
            "Authorization": "[Filtered]",
            "Cookie": "[Filtered]",
        },
        "method": "GET",
        "query_string": {"bonkers": "true"},
        "url": "https://iwsz2c7uwi.execute-api.us-east-1.amazonaws.com/asd",
        "data": None,
    }

    # Legacy send_default_pii=True attaches the user identity.
    assert transaction_event["user"] == {
        "id": "42",
        "ip_address": "213.47.147.207",
    }


URL_QUERY_PAYLOAD = b"""
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


def test_url_query_params_with_data_collection(lambda_client, test_environment):
    lambda_client.invoke(
        FunctionName="BasicOkDataCollectionUrlQueryDenylist",
        Payload=URL_QUERY_PAYLOAD,
    )
    envelopes = test_environment["server"].envelopes

    (transaction_event,) = envelopes

    assert transaction_event["request"]["query_string"] == {
        # Not denied by any term -> pass through.
        "page": "2",
        # Denied by custom terms.
        "tracking": "[Filtered]",
        # Denied by the built-in sensitive denylist.
        "token": "[Filtered]",
    }

    # Allowlist behaviour: only allowlisted, non-sensitive params pass through.
    test_environment["before_test"]()
    lambda_client.invoke(
        FunctionName="BasicOkDataCollectionUrlQueryAllowlist",
        Payload=URL_QUERY_PAYLOAD,
    )
    envelopes = test_environment["server"].envelopes

    (transaction_event,) = envelopes

    assert transaction_event["request"]["query_string"] == {
        # Allowlisted, non-sensitive -> pass through.
        "page": "2",
        # Not allowlisted -> substituted.
        "tracking": "[Filtered]",
        # Allowlisted but sensitive -> still filtered; an allowlist entry
        # cannot override the built-in sensitive denylist.
        "token": "[Filtered]",
    }

    test_environment["before_test"]()
    lambda_client.invoke(
        FunctionName="BasicOkDataCollectionUrlQueryOff",
        Payload=URL_QUERY_PAYLOAD,
    )
    envelopes = test_environment["server"].envelopes

    (transaction_event,) = envelopes

    assert "query_string" not in transaction_event["request"]


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


def test_error_trace_context(lambda_client, test_environment):
    trace_id = "471a43a4192642f0b136d5159a501701"
    parent_span_id = "6e8f22c393e68f19"
    parent_sampled = 1
    sentry_trace_header = "{}-{}-{}".format(trace_id, parent_span_id, parent_sampled)

    # We simulate here AWS Api Gateway's behavior of passing HTTP headers
    # as the `headers` dict in the event passed to the Lambda function.
    trace_payload = json.dumps({"headers": {"sentry-trace": sentry_trace_header}})

    for lambda_function_name in (
        "RaiseErrorPerformanceEnabled",
        "RaiseErrorPerformanceDisabled",
    ):
        performance_enabled = lambda_function_name == "RaiseErrorPerformanceEnabled"

        # Without an incoming sentry-trace header, the error event gets a new
        # trace context (shared with the transaction, if any).
        lambda_client.invoke(
            FunctionName=lambda_function_name,
            Payload=json.dumps({}),
        )
        envelopes = test_environment["server"].envelopes

        if performance_enabled:
            (error_event, transaction_event) = envelopes
        else:
            (error_event,) = envelopes
            transaction_event = None

        assert "trace" in error_event["contexts"]
        assert "trace_id" in error_event["contexts"]["trace"]

        if transaction_event:
            assert "trace" in transaction_event["contexts"]
            assert "trace_id" in transaction_event["contexts"]["trace"]
            assert (
                error_event["contexts"]["trace"]["trace_id"]
                == transaction_event["contexts"]["trace"]["trace_id"]
            )

        # With an incoming sentry-trace header, the existing trace is
        # continued.
        test_environment["before_test"]()
        lambda_client.invoke(
            FunctionName=lambda_function_name,
            Payload=trace_payload,
        )
        envelopes = test_environment["server"].envelopes

        if performance_enabled:
            (error_event, transaction_event) = envelopes
        else:
            (error_event,) = envelopes
            transaction_event = None

        assert "trace" in error_event["contexts"]
        assert "trace_id" in error_event["contexts"]["trace"]
        assert error_event["contexts"]["trace"]["trace_id"] == trace_id

        if transaction_event:
            assert "trace" in transaction_event["contexts"]
            assert "trace_id" in transaction_event["contexts"]["trace"]
            assert transaction_event["contexts"]["trace"]["trace_id"] == trace_id

        test_environment["before_test"]()


def _get_span_attr(attrs, key):
    """Extract the value from a span attribute, handling both flat and typed formats."""
    val = attrs[key]
    if isinstance(val, dict) and "value" in val:
        return val["value"]
    return val


def _assert_segment_span_attrs(attrs, function_name):

    arn = "arn:aws:lambda:us-east-1:012345678912:function:%s" % function_name

    assert _get_span_attr(attrs, "sentry.op") == "function.aws"
    assert _get_span_attr(attrs, "sentry.origin") == "auto.function.aws_lambda"
    assert _get_span_attr(attrs, "sentry.segment.name.source") == "component"
    assert _get_span_attr(attrs, "cloud.provider") == "aws"
    assert _get_span_attr(attrs, "cloud.platform") == "aws_lambda"
    assert _get_span_attr(attrs, "cloud.resource_id") == arn
    assert _get_span_attr(attrs, "cloud.region") == "us-east-1"
    assert _get_span_attr(attrs, "faas.name") == function_name
    assert _get_span_attr(attrs, "faas.version") == "$LATEST"
    assert "faas.invocation_id" in attrs
    assert _get_span_attr(attrs, "aws.lambda.invoked_arn") == arn
    assert _get_span_attr(attrs, "aws.log.group.names") == [
        "aws/lambda/%s" % function_name
    ]
    assert _get_span_attr(attrs, "aws.log.stream.names") == ["$LATEST"]


def test_span_streaming(lambda_client, test_environment):
    # Success case: no envelopes, one segment span with full attributes.
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
    _assert_segment_span_attrs(segment_span["attributes"], "BasicOkSpanStreaming")
    assert (
        _get_span_attr(segment_span["attributes"], "messaging.batch.message_count") == 1
    )

    # Error case: an error event plus an errored segment span.
    test_environment["before_test"]()
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
    assert exception["mechanism"]["type"] == "aws_lambda"
    assert not exception["mechanism"]["handled"]

    segment_spans = [s for s in span_items if s["is_segment"]]
    assert len(segment_spans) == 1
    segment_span = segment_spans[0]

    assert segment_span["name"] == "RaiseErrorSpanStreaming"
    assert segment_span["status"] == "error"
    _assert_segment_span_attrs(segment_span["attributes"], "RaiseErrorSpanStreaming")
    assert (
        _get_span_attr(segment_span["attributes"], "messaging.batch.message_count") == 1
    )

    # Trace continuation: an incoming sentry-trace header is continued by
    # both the error event and the streamed segment span.
    test_environment["before_test"]()
    trace_id = "471a43a4192642f0b136d5159a501701"
    parent_span_id = "6e8f22c393e68f19"
    parent_sampled = 1
    sentry_trace_header = "{}-{}-{}".format(trace_id, parent_span_id, parent_sampled)

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
    _assert_segment_span_attrs(segment_span["attributes"], "RaiseErrorSpanStreaming")


def test_span_streaming_request_attributes(lambda_client, test_environment):
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
        FunctionName="BasicOkSpanStreamingPii",
        Payload=json.dumps(payload),
    )
    span_items = test_environment["server"].span_items

    segment_spans = [s for s in span_items if s["is_segment"]]
    assert len(segment_spans) == 1
    segment_span = segment_spans[0]
    attrs = segment_span["attributes"]

    assert _get_span_attr(attrs, "http.request.method") == "POST"
    assert (
        _get_span_attr(attrs, "url.query")
        == "foo=bar&a-complicated-value=a%3Db%26c%3Dd"
    )
    assert (
        _get_span_attr(attrs, "http.request.header.content-type") == "application/json"
    )
    assert _get_span_attr(attrs, "http.request.header.accept") == "text/html"
    assert _get_span_attr(attrs, "faas.name") == "BasicOkSpanStreamingPii"
    assert _get_span_attr(attrs, "cloud.provider") == "aws"
    assert _get_span_attr(attrs, "cloud.platform") == "aws_lambda"
    assert _get_span_attr(attrs, "cloud.region") == "us-east-1"
    assert _get_span_attr(attrs, "faas.version") == "$LATEST"
    assert "faas.invocation_id" in attrs
    assert _get_span_attr(attrs, "aws.log.group.names") == [
        "aws/lambda/BasicOkSpanStreamingPii"
    ]
    assert _get_span_attr(attrs, "aws.log.stream.names") == ["$LATEST"]

    test_environment["before_test"]()
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
    span_items = test_environment["server"].span_items

    segment_spans = [s for s in span_items if s["is_segment"]]
    assert len(segment_spans) == 1
    segment_span = segment_spans[0]
    attrs = segment_span["attributes"]

    assert (
        _get_span_attr(attrs, "url.query")
        == "page=2&tracking=%5BFiltered%5D&token=%5BFiltered%5D"
    )
