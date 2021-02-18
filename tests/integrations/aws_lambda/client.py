import sys
import os
import shutil
import tempfile
import subprocess
import boto3
import uuid
import base64


def get_boto_client():
    return boto3.client(
        "lambda",
        aws_access_key_id=os.environ["SENTRY_PYTHON_TEST_AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["SENTRY_PYTHON_TEST_AWS_SECRET_ACCESS_KEY"],
        region_name="us-east-1",
    )


def build_no_code_serverless_function_and_layer(
    client, tmpdir, fn_name, runtime, timeout
):
    """
    Util function that auto instruments the no code implementation of the python
    sdk by creating a layer containing the Python-sdk, and then creating a func
    that uses that layer
    """
    from scripts.build_awslambda_layer import (
        build_packaged_zip,
    )

    build_packaged_zip(dest_abs_path=tmpdir, dest_zip_filename="serverless-ball.zip")

    with open(os.path.join(tmpdir, "serverless-ball.zip"), "rb") as serverless_zip:
        response = client.publish_layer_version(
            LayerName="python-serverless-sdk-test",
            Description="Created as part of testsuite for getsentry/sentry-python",
            Content={"ZipFile": serverless_zip.read()},
        )

    with open(os.path.join(tmpdir, "ball.zip"), "rb") as zip:
        client.create_function(
            FunctionName=fn_name,
            Runtime=runtime,
            Timeout=timeout,
            Environment={
                "Variables": {
                    "SENTRY_INITIAL_HANDLER": "test_lambda.test_handler",
                    "SENTRY_DSN": "https://123abc@example.com/123",
                    "SENTRY_TRACES_SAMPLE_RATE": "1.0"
                }
            },
            Role=os.environ["SENTRY_PYTHON_TEST_AWS_IAM_ROLE"],
            Handler="sentry_sdk.integrations.init_serverless_sdk.sentry_lambda_handler",
            Layers=[response["LayerVersionArn"]],
            Code={"ZipFile": zip.read()},
            Description="Created as part of testsuite for getsentry/sentry-python",
        )


def run_lambda_function(
    client,
    runtime,
    code,
    payload,
    add_finalizer,
    syntax_check=True,
    timeout=30,
    layer=None,
    subprocess_kwargs=(),
):
    subprocess_kwargs = dict(subprocess_kwargs)

    with tempfile.TemporaryDirectory() as tmpdir:
        test_lambda_py = os.path.join(tmpdir, "test_lambda.py")
        with open(test_lambda_py, "w") as f:
            f.write(code)

        if syntax_check:
            # Check file for valid syntax first, and that the integration does not
            # crash when not running in Lambda (but rather a local deployment tool
            # such as chalice's)
            subprocess.check_call([sys.executable, test_lambda_py])

        fn_name = "test_function_{}".format(uuid.uuid4())

        if layer is None:
            setup_cfg = os.path.join(tmpdir, "setup.cfg")
            with open(setup_cfg, "w") as f:
                f.write("[install]\nprefix=")

            subprocess.check_call(
                [sys.executable, "setup.py", "sdist", "-d", os.path.join(tmpdir, "..")],
                **subprocess_kwargs
            )

            subprocess.check_call(
                "pip install mock==3.0.0 funcsigs -t .",
                cwd=tmpdir,
                shell=True,
                **subprocess_kwargs
            )

            # https://docs.aws.amazon.com/lambda/latest/dg/lambda-python-how-to-create-deployment-package.html
            subprocess.check_call(
                "pip install ../*.tar.gz -t .",
                cwd=tmpdir,
                shell=True,
                **subprocess_kwargs
            )

            shutil.make_archive(os.path.join(tmpdir, "ball"), "zip", tmpdir)

            with open(os.path.join(tmpdir, "ball.zip"), "rb") as zip:
                client.create_function(
                    FunctionName=fn_name,
                    Runtime=runtime,
                    Timeout=timeout,
                    Role=os.environ["SENTRY_PYTHON_TEST_AWS_IAM_ROLE"],
                    Handler="test_lambda.test_handler",
                    Code={"ZipFile": zip.read()},
                    Description="Created as part of testsuite for getsentry/sentry-python",
                )
        else:
            subprocess.run(
                ["zip", "-q", "-x", "**/__pycache__/*", "-r", "ball.zip", "./"],
                cwd=tmpdir,
                check=True,
            )
            build_no_code_serverless_function_and_layer(
                client, tmpdir, fn_name, runtime, timeout
            )

        @add_finalizer
        def clean_up():
            client.delete_function(FunctionName=fn_name)

            # this closes the web socket so we don't get a
            #   ResourceWarning: unclosed <ssl.SSLSocket ... >
            # warning on every test
            # based on https://github.com/boto/botocore/pull/1810
            # (if that's ever merged, this can just become client.close())
            session = client._endpoint.http_session
            managers = [session._manager] + list(session._proxy_managers.values())
            for manager in managers:
                manager.clear()

        response = client.invoke(
            FunctionName=fn_name,
            InvocationType="RequestResponse",
            LogType="Tail",
            Payload=payload,
        )

        assert 200 <= response["StatusCode"] < 300, response
        return response


_REPL_CODE = """
import os

def test_handler(event, context):
    line = {line!r}
    if line.startswith(">>> "):
        exec(line[4:])
    elif line.startswith("$ "):
        os.system(line[2:])
    else:
        print("Start a line with $ or >>>")

    return b""
"""

try:
    import click
except ImportError:
    pass
else:

    @click.command()
    @click.option(
        "--runtime", required=True, help="name of the runtime to use, eg python3.8"
    )
    @click.option("--verbose", is_flag=True, default=False)
    def repl(runtime, verbose):
        """
        Launch a "REPL" against AWS Lambda to inspect their runtime.
        """

        cleanup = []
        client = get_boto_client()

        print("Start a line with `$ ` to run shell commands, or `>>> ` to run Python")

        while True:
            line = input()

            response = run_lambda_function(
                client,
                runtime,
                _REPL_CODE.format(line=line),
                b"",
                cleanup.append,
                subprocess_kwargs={
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                }
                if not verbose
                else {},
            )

            for line in base64.b64decode(response["LogResult"]).splitlines():
                print(line.decode("utf8"))

            for f in cleanup:
                f()

            cleanup = []

    if __name__ == "__main__":
        repl()
