import boto3
import json
import pytest
import requests
import subprocess
import time
import yaml
from aws_cdk import (
    App,
    CfnResource,
    Stack,
)
from constructs import Construct


SAM_PORT = 3001
SAM_REGION = "us-east-1"
SAM_TEMPLATE_FILE = "sam.template.yaml"


class DummyLambdaStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Override the template synthesis
        self.template_options.template_format_version = "2010-09-09"
        self.template_options.transforms = ["AWS::Serverless-2016-10-31"]

        # Add the function using SAM format
        CfnResource(
            self,
            "BasicTestFunction",
            type="AWS::Serverless::Function",
            properties={
                "CodeUri": "./tests/integrations/aws_lambda/lambda_functions/hello_world",
                "Handler": "index.handler",
                "Runtime": "python3.12",
            },
        )


def wait_for_sam(timeout=30, port=SAM_PORT):
    """
    Wait for SAM to be ready, with timeout.
    """
    start_time = time.time()
    while True:
        if time.time() - start_time > timeout:
            raise TimeoutError("SAM failed to start within {} seconds".format(timeout))

        try:
            # Try to connect to SAM
            response = requests.get(f"http://127.0.0.1:{port}/")
            if response.status_code == 200 or response.status_code == 404:
                return

        except requests.exceptions.ConnectionError:
            time.sleep(1)
            continue


@pytest.fixture(scope="session")
def sam_stack():
    """
    Create and deploy the SAM stack once for all tests
    """
    app = App()
    stack = DummyLambdaStack(app, "DummyLambdaStack", env={"region": SAM_REGION})

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
            "--region",
            SAM_REGION,
            "--template",
            SAM_TEMPLATE_FILE,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,  # This makes stdout/stderr return strings instead of bytes
    )

    try:
        # Wait for SAM to be ready
        wait_for_sam()
        yield stack
    finally:
        process.terminate()
        process.wait(timeout=5)  # Give it time to shut down gracefully

        # Force kill if still running
        if process.poll() is None:
            process.kill()


@pytest.fixture
def lambda_client():
    """
    Create a boto3 client configured to use SAM local
    """
    return boto3.client(
        "lambda",
        endpoint_url=f"http://127.0.0.1:{SAM_PORT}",
        region_name=SAM_REGION,
        aws_access_key_id="dummy",
        aws_secret_access_key="dummy",
    )


def test_basic(lambda_client, sam_stack):
    region = boto3.Session().region_name
    print("Region: ", region)

    response = lambda_client.invoke(
        FunctionName="BasicTestFunction", Payload=json.dumps({"name": "Anton"})
    )
    result = json.loads(response["Payload"].read().decode())

    assert result == {"message": "Hello, Anton!"}
