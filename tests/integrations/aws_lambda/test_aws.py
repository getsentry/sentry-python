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

from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration, get_lambda_bootstrap
import sentry_sdk
import json
import time

from sentry_sdk.transport import HttpTransport

def event_processor(event):
    # AWS Lambda truncates the log output to 4kb, which is small enough to miss
    # parts of even a single error-event/transaction-envelope pair if considered
    # in full, so only grab the data we need.

    event_data = {}
    event_data["contexts"] = {}
    event_data["contexts"]["trace"] = event.get("contexts", {}).get("trace")
    event_data["exception"] = event.get("exception")
    event_data["extra"] = event.get("extra")
    event_data["level"] = event.get("level")
    event_data["request"] = event.get("request")
    event_data["tags"] = event.get("tags")
    event_data["transaction"] = event.get("transaction")

    return event_data

def envelope_processor(envelope):
    # AWS Lambda truncates the log output to 4kb, which is small enough to miss
    # parts of even a single error-event/transaction-envelope pair if considered
    # in full, so only grab the data we need.

    (item,) = envelope.items
    envelope_json = json.loads(item.get_bytes())

    envelope_data = {}
    envelope_data["contexts"] = {}
    envelope_data["type"] = envelope_json["type"]
    envelope_data["transaction"] = envelope_json["transaction"]
    envelope_data["contexts"]["trace"] = envelope_json["contexts"]["trace"]
    envelope_data["request"] = envelope_json["request"]
    envelope_data["tags"] = envelope_json["tags"]

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


@pytest.fixture(
    params=["python3.6", "python3.7", "python3.8", "python3.9", "python2.7"]
)
def lambda_runtime(request):
    return request.param


@pytest.fixture
def run_lambda_function(request, lambda_client, lambda_runtime):
    def inner(
        code, payload, timeout=30, syntax_check=True, layer=None, initial_handler=None
    ):
        from tests.integrations.aws_lambda.client import run_lambda_function

        response = run_lambda_function(
            client=lambda_client,
            runtime=lambda_runtime,
            code=code,
            payload=payload,
            add_finalizer=request.addfinalizer,
            timeout=timeout,
            syntax_check=syntax_check,
            layer=layer,
            initial_handler=initial_handler,
        )

        # for better debugging
        response["LogResult"] = base64.b64decode(response["LogResult"]).splitlines()
        response["Payload"] = json.loads(response["Payload"].read().decode("utf-8"))
        del response["ResponseMetadata"]

        events = []
        envelopes = []

        for line in response["LogResult"]:
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
    assert envelope["transaction_info"] == {"source": "component"}
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
    assert envelope["transaction_info"] == {"source": "component"}
    assert envelope["transaction"] in envelope["request"]["url"]


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
                        "Host": "dogs.are.great",
                        "X-Forwarded-Proto": "http"
                    },
                    "httpMethod": "GET",
                    "path": "/tricks/kangaroo",
                    "queryStringParameters": {
                        "completed_successfully": "true",
                        "treat_provided": "true",
                        "treat_type": "cheese"
                    },
                    "dog": "Maisey"
                },
                {
                    "headers": {
                        "Host": "dogs.are.great",
                        "X-Forwarded-Proto": "http"
                    },
                    "httpMethod": "GET",
                    "path": "/tricks/kangaroo",
                    "queryStringParameters": {
                        "completed_successfully": "true",
                        "treat_provided": "true",
                        "treat_type": "cheese"
                    },
                    "dog": "Charlie"
                }
            ]
            """,
            True,
            2,
        ),
    ],
)
def test_non_dict_event(
    run_lambda_function,
    aws_event,
    has_request_data,
    batch_size,
    DictionaryContaining,  # noqa:N803
):
    envelopes, events, response = run_lambda_function(
        LAMBDA_PRELUDE
        + dedent(
            """
        init_sdk(traces_sample_rate=1.0)

        def test_handler(event, context):
            raise Exception("More treats, please!")
        """
        ),
        aws_event,
    )

    assert response["FunctionError"] == "Unhandled"

    error_event = events[0]
    assert error_event["level"] == "error"
    assert error_event["contexts"]["trace"]["op"] == "serverless.function"

    function_name = error_event["extra"]["lambda"]["function_name"]
    assert function_name.startswith("test_function_")
    assert error_event["transaction"] == function_name

    exception = error_event["exception"]["values"][0]
    assert exception["type"] == "Exception"
    assert exception["value"] == "More treats, please!"
    assert exception["mechanism"]["type"] == "aws_lambda"

    envelope = envelopes[0]
    assert envelope["type"] == "transaction"
    assert envelope["contexts"]["trace"] == DictionaryContaining(
        error_event["contexts"]["trace"]
    )
    assert envelope["contexts"]["trace"]["status"] == "internal_error"
    assert envelope["transaction"] == error_event["transaction"]
    assert envelope["request"]["url"] == error_event["request"]["url"]

    if has_request_data:
        request_data = {
            "headers": {"Host": "dogs.are.great", "X-Forwarded-Proto": "http"},
            "method": "GET",
            "url": "http://dogs.are.great/tricks/kangaroo",
            "query_string": {
                "completed_successfully": "true",
                "treat_provided": "true",
                "treat_type": "cheese",
            },
        }
    else:
        request_data = {"url": "awslambda:///{}".format(function_name)}

    assert error_event["request"] == request_data
    assert envelope["request"] == request_data

    if batch_size > 1:
        assert error_event["tags"]["batch_size"] == batch_size
        assert error_event["tags"]["batch_request"] is True
        assert envelope["tags"]["batch_size"] == batch_size
        assert envelope["tags"]["batch_request"] is True


def test_traces_sampler_gets_correct_values_in_sampling_context(
    run_lambda_function,
    DictionaryContaining,  # noqa:N803
    ObjectDescribedBy,  # noqa:N803
    StringContaining,  # noqa:N803
):
    # TODO: This whole thing is a little hacky, specifically around the need to
    # get `conftest.py` code into the AWS runtime, which is why there's both
    # `inspect.getsource` and a copy of `_safe_is_equal` included directly in
    # the code below. Ideas which have been discussed to fix this:

    # - Include the test suite as a module installed in the package which is
    #   shot up to AWS
    # - In client.py, copy `conftest.py` (or wherever the necessary code lives)
    #   from the test suite into the main SDK directory so it gets included as
    #   "part of the SDK"

    # It's also worth noting why it's necessary to run the assertions in the AWS
    # runtime rather than asserting on side effects the way we do with events
    # and envelopes. The reasons are two-fold:

    # - We're testing against the `LambdaContext` class, which only exists in
    #   the AWS runtime
    # - If we were to transmit call args data they way we transmit event and
    #   envelope data (through JSON), we'd quickly run into the problem that all
    #   sorts of stuff isn't serializable by `json.dumps` out of the box, up to
    #   and including `datetime` objects (so anything with a timestamp is
    #   automatically out)

    # Perhaps these challenges can be solved in a cleaner and more systematic
    # way if we ever decide to refactor the entire AWS testing apparatus.

    import inspect

    envelopes, events, response = run_lambda_function(
        LAMBDA_PRELUDE
        + dedent(inspect.getsource(StringContaining))
        + dedent(inspect.getsource(DictionaryContaining))
        + dedent(inspect.getsource(ObjectDescribedBy))
        + dedent(
            """
            try:
                from unittest import mock  # python 3.3 and above
            except ImportError:
                import mock  # python < 3.3

            def _safe_is_equal(x, y):
                # copied from conftest.py - see docstring and comments there
                try:
                    is_equal = x.__eq__(y)
                except AttributeError:
                    is_equal = NotImplemented

                if is_equal == NotImplemented:
                    # using == smoothes out weird variations exposed by raw __eq__
                    return x == y

                return is_equal

            def test_handler(event, context):
                # this runs after the transaction has started, which means we
                # can make assertions about traces_sampler
                try:
                    traces_sampler.assert_any_call(
                        DictionaryContaining(
                            {
                                "aws_event": DictionaryContaining({
                                    "httpMethod": "GET",
                                    "path": "/sit/stay/rollover",
                                    "headers": {"Host": "dogs.are.great", "X-Forwarded-Proto": "http"},
                                }),
                                "aws_context": ObjectDescribedBy(
                                    type=get_lambda_bootstrap().LambdaContext,
                                    attrs={
                                        'function_name': StringContaining("test_function"),
                                        'function_version': '$LATEST',
                                    }
                                )
                            }
                        )
                    )
                except AssertionError:
                    # catch the error and return it because the error itself will
                    # get swallowed by the SDK as an "internal exception"
                    return {"AssertionError raised": True,}

                return {"AssertionError raised": False,}


            traces_sampler = mock.Mock(return_value=True)

            init_sdk(
                traces_sampler=traces_sampler,
            )
        """
        ),
        b'{"httpMethod": "GET", "path": "/sit/stay/rollover", "headers": {"Host": "dogs.are.great", "X-Forwarded-Proto": "http"}}',
    )

    assert response["Payload"]["AssertionError raised"] is False


def test_serverless_no_code_instrumentation(run_lambda_function):
    """
    Test that ensures that just by adding a lambda layer containing the
    python sdk, with no code changes sentry is able to capture errors
    """

    for initial_handler in [
        None,
        "test_dir/test_lambda.test_handler",
        "test_dir.test_lambda.test_handler",
    ]:
        print("Testing Initial Handler ", initial_handler)
        _, _, response = run_lambda_function(
            dedent(
                """
            import sentry_sdk

            def test_handler(event, context):
                current_client = sentry_sdk.Hub.current.client

                assert current_client is not None

                assert len(current_client.options['integrations']) == 1
                assert isinstance(current_client.options['integrations'][0],
                                  sentry_sdk.integrations.aws_lambda.AwsLambdaIntegration)

                raise Exception("something went wrong")
            """
            ),
            b'{"foo": "bar"}',
            layer=True,
            initial_handler=initial_handler,
        )
        assert response["FunctionError"] == "Unhandled"
        assert response["StatusCode"] == 200

        assert response["Payload"]["errorType"] != "AssertionError"

        assert response["Payload"]["errorType"] == "Exception"
        assert response["Payload"]["errorMessage"] == "something went wrong"

        assert "sentry_handler" in response["LogResult"][3].decode("utf-8")
