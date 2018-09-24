import base64
import json
import os
import shutil
import subprocess
import sys
import uuid

import pytest

boto3 = pytest.importorskip("boto3")

LAMBDA_TEMPLATE = """
from __future__ import print_function

from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration
import sentry_sdk
import json
from sentry_sdk.transport import Transport

class TestTransport(Transport):
    def __init__(self):
        Transport.__init__(self)
        self._queue = []

    def capture_event(self, event):
        self._queue.append(event)

    def shutdown(self, timeout, callback=None):
        # Delay event output like this to test proper shutdown
        for event in self._queue:
            print("EVENT:", json.dumps(event))


sentry_sdk.init(
    "http://bogus@example.com/2",
    transport=TestTransport(),
    integrations=[AwsLambdaIntegration()],
    **{extra_init_args}
)

def test_handler(event, context):
    exec('''{code}''')
"""


@pytest.fixture
def lambda_client():
    if "AWS_ACCESS_KEY_ID" not in os.environ:
        pytest.skip("AWS environ vars not set")

    return boto3.client(
        "lambda",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name="us-east-1",
    )


@pytest.fixture(params=["python3.6", "python2.7"])
def run_lambda_function(tmpdir, lambda_client, request, assert_semaphore_acceptance):
    def inner(lambda_body, payload, extra_init_args=None):
        tmpdir.ensure_dir("lambda_tmp").remove()
        tmp = tmpdir.ensure_dir("lambda_tmp")

        # https://docs.aws.amazon.com/lambda/latest/dg/lambda-python-how-to-create-deployment-package.html
        tmp.join("test_lambda.py").write(
            LAMBDA_TEMPLATE.format(
                code=lambda_body, extra_init_args=repr(extra_init_args or {})
            )
        )
        tmp.join("setup.cfg").write("[install]\nprefix=")
        subprocess.check_call([sys.executable, "setup.py", "sdist", "-d", str(tmpdir)])
        subprocess.check_call("pip install ../*.tar.gz -t .", cwd=str(tmp), shell=True)
        shutil.make_archive(tmpdir.join("ball"), "zip", str(tmp))

        fn_name = "test_function_{}".format(uuid.uuid4())

        lambda_client.create_function(
            FunctionName=fn_name,
            Runtime=request.param,
            Role=os.environ["AWS_IAM_ROLE"],
            Handler="test_lambda.test_handler",
            Code={"ZipFile": tmpdir.join("ball.zip").read(mode="rb")},
            Description="Created as part of testsuite for getsentry/sentry-python",
        )

        @request.addfinalizer
        def delete_function():
            lambda_client.delete_function(FunctionName=fn_name)

        response = lambda_client.invoke(
            FunctionName=fn_name,
            InvocationType="RequestResponse",
            LogType="Tail",
            Payload=payload,
        )

        assert 200 <= response["StatusCode"] < 300, response

        events = []

        for line in base64.b64decode(response["LogResult"]).splitlines():
            print("AWS:", line)
            if not line.startswith(b"EVENT: "):
                continue
            line = line[len(b"EVENT: ") :]
            events.append(json.loads(line.decode("utf-8")))
            assert_semaphore_acceptance(events[-1])

        return events, response

    return inner


def test_basic(run_lambda_function):
    events, response = run_lambda_function(
        'raise Exception("something went wrong")\n', b'{"foo": "bar"}'
    )

    assert response["FunctionError"] == "Unhandled"

    event, = events
    assert event["level"] == "error"
    exception, = event["exception"]["values"]
    assert exception["type"] == "Exception"
    assert exception["value"] == "something went wrong"

    frame1, frame2 = exception["stacktrace"]["frames"]
    assert frame1["filename"] == "test_lambda.py"
    assert frame1["abs_path"] == "/var/task/test_lambda.py"
    assert frame1["function"] == "test_handler"

    assert frame1["in_app"] is frame2["in_app"] is True

    assert exception["mechanism"] == {"type": "aws_lambda", "handled": False}

    assert event["extra"]["lambda"]["function_name"].startswith("test_function_")


def test_request_body_omitted(run_lambda_function):
    events, response = run_lambda_function(
        'raise Exception("something went wrong")\n',
        payload=b'{"foo": "bar"}',
        extra_init_args={"request_bodies": "never"},
    )

    event, = events
    assert "data" not in event.get("request", {})


def test_request_data(run_lambda_function):
    events, _response = run_lambda_function(
        'sentry_sdk.capture_message("hi")\n' 'return "ok"\n',
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

    event, = events

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
