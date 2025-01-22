import boto3
import json
import pytest
import requests
import subprocess
import time
import threading
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

        # Create Sentry Lambda Layer
        layer = CfnResource(
            self,
            "SentryPythonServerlessSDK",
            type="AWS::Serverless::LayerVersion",
            properties={
                "ContentUri": "./tests/integrations/aws_lambda/lambda_layer",
                "CompatibleRuntimes": [
                    "python3.7",
                    "python3.8",
                    "python3.9",
                    "python3.10",
                    "python3.11",
                    "python3.12",
                    "python3.13",
                ],
            },
        )

        # Add the function using SAM format
        CfnResource(
            self,
            "BasicTestFunction",
            type="AWS::Serverless::Function",
            properties={
                "CodeUri": "./tests/integrations/aws_lambda/lambda_functions/hello_world",
                "Handler": "index.handler",
                "Runtime": "python3.12",
                "Layers": [{"Ref": layer.logical_id}],
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


class SentryTestServer:
    def __init__(self, port=9999):
        self.envelopes = []
        self.port = port

        from fastapi import FastAPI, Request

        self.app = FastAPI()

        @self.app.get("/")
        async def root():
            return {
                "message": "Sentry Test Server. Use DSN http://123@localhost:9999/0 in your SDK."
            }

        @self.app.post("/api/0/envelope/")
        async def envelope(request: Request):
            self.envelopes.append(await request.json())
            return {"status": "ok"}

    def run_server(self):
        import uvicorn

        uvicorn.run(self.app, host="0.0.0.0", port=self.port)

    def start(self):
        server_thread = threading.Thread(target=self.run_server, daemon=True)
        server_thread.start()

    def clear_envelopes(self):
        self.envelopes = []


@pytest.fixture(scope="session")
def sentry_test_server():
    server = SentryTestServer()
    server.start()

    time.sleep(1)  # Give it a moment to start up

    yield server


@pytest.fixture(scope="session")
def sam_stack():
    """
    Create and deploy the SAM stack once for all tests
    """
    # Create the SAM stack
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


def test_basic(lambda_client, sam_stack, sentry_test_server):
    sentry_test_server.clear_envelopes()

    response = lambda_client.invoke(
        FunctionName="BasicTestFunction", Payload=json.dumps({"name": "Anton"})
    )
    result = json.loads(response["Payload"].read().decode())

    print(sentry_test_server.envelopes)
    assert result == {"message": "Hello, Anton!"}


def test_basic_2(lambda_client, sam_stack, sentry_test_server):
    sentry_test_server.clear_envelopes()

    response = lambda_client.invoke(
        FunctionName="BasicTestFunction", Payload=json.dumps({"name": "Anton"})
    )
    result = json.loads(response["Payload"].read().decode())

    print(sentry_test_server.envelopes)
    assert result == {"message": "Hello, Anton!"}
