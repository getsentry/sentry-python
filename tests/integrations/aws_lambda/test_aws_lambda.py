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
            print("[TEST] Clearing envelopes before test")

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


def test_basic(lambda_client, test_environment):
    response = lambda_client.invoke(
        FunctionName="BasicTestFunction", Payload=json.dumps({"name": "Ivana"})
    )
    result = json.loads(response["Payload"].read().decode())
    assert result == {"message": "Hello, Ivana!"}

    message, transaction = test_environment["server"].envelopes
    assert message["message"] == "[SENTRY MESSAGE] Hello, Ivana!"
    assert transaction["type"] == "transaction"


def test_basic_2(lambda_client, test_environment):
    test_environment["server"].clear_envelopes()

    response = lambda_client.invoke(
        FunctionName="BasicTestFunction", Payload=json.dumps({"name": "Neel"})
    )
    result = json.loads(response["Payload"].read().decode())
    assert result == {"message": "Hello, Neel!"}

    message, transaction = test_environment["server"].envelopes
    assert message["message"] == "[SENTRY MESSAGE] Hello, Neel!"
    assert transaction["type"] == "transaction"
