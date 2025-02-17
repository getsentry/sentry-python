import boto3
import json
import pytest
import subprocess
import time
import yaml

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

    # Start SAM local
    process = subprocess.Popen(
        [
            "sam",
            "local",
            "start-lambda",
            "--template",
            SAM_TEMPLATE_FILE,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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


# def test_basic_ok(lambda_client, test_environment):
#     response = lambda_client.invoke(
#         FunctionName="BasicOk", 
#         Payload=json.dumps({"name": "Ivana"}),
#     )
#     result = json.loads(response["Payload"].read().decode())
#     assert result == {"event": {"name": "Ivana"}}

#     envelopes = test_environment["server"].envelopes
#     assert len(envelopes) == 1

#     transaction = envelopes[0]
#     assert transaction["type"] == "transaction"


def test_xxx(lambda_client, test_environment):
    for x in range(20):   
        test_environment["server"].clear_envelopes()
        print(f"*** BasicException {x} ***")
        response = lambda_client.invoke(
            FunctionName="BasicException", 
            Payload=json.dumps({}),
        )
        print("- RESPONSE")
        print(response)
        print("- PAYLOAD")
        print(response["Payload"].read().decode())
        print(f'- ENVELOPES {len(test_environment["server"].envelopes)}')



    assert False


# def test_basic(lambda_client, test_environment):
#     response = lambda_client.invoke(
#         FunctionName="BasicException", 
#         Payload=json.dumps({"name": "Neel"}),
#     )
#     print("RESPONSE")
#     print(response)
#     print("PAYLOAD")
#     print(response["Payload"].read().decode())
#     result = json.loads(response["Payload"].read().decode())
#     print("RESULT")
#     print(result)

#     envelopes = test_environment["server"].envelopes
#     (error,) = envelopes

#     assert error["level"] == "error"
#     (exception,) = error["exception"]["values"]
#     assert exception["type"] == "Exception"
#     assert exception["value"] == "Oh!"

#     (frame1,) = exception["stacktrace"]["frames"]
#     assert frame1["filename"] == "test_lambda.py"
#     assert frame1["abs_path"] == "/var/task/test_lambda.py"
#     assert frame1["function"] == "test_handler"

#     assert frame1["in_app"] is True

#     assert exception["mechanism"]["type"] == "aws_lambda"
#     assert not exception["mechanism"]["handled"]

#     assert error["extra"]["lambda"]["function_name"].startswith("test_")

#     logs_url = error["extra"]["cloudwatch logs"]["url"]
#     assert logs_url.startswith("https://console.aws.amazon.com/cloudwatch/home?region")
#     assert not re.search("(=;|=$)", logs_url)
#     assert error["extra"]["cloudwatch logs"]["log_group"].startswith(
#         "/aws/lambda/test_"
#     )

#     log_stream_re = "^[0-9]{4}/[0-9]{2}/[0-9]{2}/\\[[^\\]]+][a-f0-9]+$"
#     log_stream = error["extra"]["cloudwatch logs"]["log_stream"]

#     assert re.match(log_stream_re, log_stream)
