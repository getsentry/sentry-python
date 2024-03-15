import base64
import boto3
import glob
import hashlib
import os
import subprocess
import sys
import tempfile

from sentry_sdk.consts import VERSION as SDK_VERSION
from sentry_sdk.utils import get_git_revision

AWS_REGION_NAME = "us-east-1"
AWS_CREDENTIALS = {
    "aws_access_key_id": os.environ["SENTRY_PYTHON_TEST_AWS_ACCESS_KEY_ID"],
    "aws_secret_access_key": os.environ["SENTRY_PYTHON_TEST_AWS_SECRET_ACCESS_KEY"],
}
AWS_LAMBDA_EXECUTION_ROLE_NAME = "lambda-ex"
AWS_LAMBDA_EXECUTION_ROLE_ARN = None


def _install_dependencies(base_dir, subprocess_kwargs):
    """
    Installs dependencies for AWS Lambda function
    """
    setup_cfg = os.path.join(base_dir, "setup.cfg")
    with open(setup_cfg, "w") as f:
        f.write("[install]\nprefix=")

    # Install requirements for Lambda Layer (these are more limited than the SDK requirements,
    # because Lambda does not support the newest versions of some packages)
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            "aws-lambda-layer-requirements.txt",
            "--target",
            base_dir,
        ],
        **subprocess_kwargs,
    )
    # Install requirements used for testing
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "mock==3.0.0",
            "funcsigs",
            "--target",
            base_dir,
        ],
        **subprocess_kwargs,
    )
    # Create a source distribution of the Sentry SDK (in parent directory of base_dir)
    subprocess.check_call(
        [
            sys.executable,
            "setup.py",
            "sdist",
            "--dist-dir",
            os.path.dirname(base_dir),
        ],
        **subprocess_kwargs,
    )
    # Install the created Sentry SDK source distribution into the target directory
    # Do not install the dependencies of the SDK, because they where installed by aws-lambda-layer-requirements.txt above
    source_distribution_archive = glob.glob(
        "{}/*.tar.gz".format(os.path.dirname(base_dir))
    )[0]
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            source_distribution_archive,
            "--no-deps",
            "--target",
            base_dir,
        ],
        **subprocess_kwargs,
    )


def _create_lambda_function_zip(base_dir):
    """
    Zips the given base_dir omitting Python cache files
    """
    subprocess.run(
        [
            "zip",
            "-q",
            "-x",
            "**/__pycache__/*",
            "-r",
            "lambda-function-package.zip",
            "./",
        ],
        cwd=base_dir,
        check=True,
    )


def _create_lambda_package(
    base_dir, code, initial_handler, layer, syntax_check, subprocess_kwargs
):
    """
    Creates deployable packages (as zip files) for AWS Lambda function
    and optional the accompanying Sentry Lambda layer
    """
    if initial_handler:
        # If Initial handler value is provided i.e. it is not the default
        # `test_lambda.test_handler`, then create another dir level so that our path is
        # test_dir.test_lambda.test_handler
        test_dir_path = os.path.join(base_dir, "test_dir")
        python_init_file = os.path.join(test_dir_path, "__init__.py")
        os.makedirs(test_dir_path)
        with open(python_init_file, "w"):
            # Create __init__ file to make it a python package
            pass

        test_lambda_py = os.path.join(base_dir, "test_dir", "test_lambda.py")
    else:
        test_lambda_py = os.path.join(base_dir, "test_lambda.py")

    with open(test_lambda_py, "w") as f:
        f.write(code)

    if syntax_check:
        # Check file for valid syntax first, and that the integration does not
        # crash when not running in Lambda (but rather a local deployment tool
        # such as chalice's)
        subprocess.check_call([sys.executable, test_lambda_py])

    if layer is None:
        _install_dependencies(base_dir, subprocess_kwargs)
        _create_lambda_function_zip(base_dir)

    else:
        _create_lambda_function_zip(base_dir)

        # Create Lambda layer zip package
        from scripts.build_aws_lambda_layer import build_packaged_zip

        build_packaged_zip(
            base_dir=base_dir,
            make_dist=True,
            out_zip_filename="lambda-layer-package.zip",
        )


def _get_or_create_lambda_execution_role():
    global AWS_LAMBDA_EXECUTION_ROLE_ARN

    policy = """{
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Service": "lambda.amazonaws.com"
                },
                "Action": "sts:AssumeRole"
            }
        ]
    }
    """
    iam_client = boto3.client(
        "iam",
        region_name=AWS_REGION_NAME,
        **AWS_CREDENTIALS,
    )

    try:
        response = iam_client.get_role(RoleName=AWS_LAMBDA_EXECUTION_ROLE_NAME)
        AWS_LAMBDA_EXECUTION_ROLE_ARN = response["Role"]["Arn"]
    except iam_client.exceptions.NoSuchEntityException:
        # create role for lambda execution
        response = iam_client.create_role(
            RoleName=AWS_LAMBDA_EXECUTION_ROLE_NAME,
            AssumeRolePolicyDocument=policy,
        )
        AWS_LAMBDA_EXECUTION_ROLE_ARN = response["Role"]["Arn"]

        # attach policy to role
        iam_client.attach_role_policy(
            RoleName=AWS_LAMBDA_EXECUTION_ROLE_NAME,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )


def get_boto_client():
    _get_or_create_lambda_execution_role()

    return boto3.client(
        "lambda",
        region_name=AWS_REGION_NAME,
        **AWS_CREDENTIALS,
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
    initial_handler=None,
    subprocess_kwargs=(),
):
    """
    Creates a Lambda function with the given code, and invokes it.

    If the same code is run multiple times the function will NOT be
    created anew each time but the existing function will be reused.
    """
    subprocess_kwargs = dict(subprocess_kwargs)

    # Making a unique function name depending on all the code that is run in it (function code plus SDK version)
    # The name needs to be short so the generated event/envelope json blobs are small enough to be output
    # in the log result of the Lambda function.
    rev = get_git_revision() or SDK_VERSION
    function_hash = hashlib.shake_256((code + rev).encode("utf-8")).hexdigest(6)
    fn_name = "test_{}".format(function_hash)
    full_fn_name = "{}_{}".format(
        fn_name, runtime.replace(".", "").replace("python", "py")
    )

    function_exists_in_aws = True
    try:
        client.get_function(
            FunctionName=full_fn_name,
        )
        print(
            "Lambda function in AWS already existing, taking it (and do not create a local one)"
        )
    except client.exceptions.ResourceNotFoundException:
        function_exists_in_aws = False

    if not function_exists_in_aws:
        tmp_base_dir = tempfile.gettempdir()
        base_dir = os.path.join(tmp_base_dir, fn_name)
        dir_already_existing = os.path.isdir(base_dir)

        if dir_already_existing:
            print("Local Lambda function directory already exists, skipping creation")

        if not dir_already_existing:
            os.mkdir(base_dir)
            _create_lambda_package(
                base_dir, code, initial_handler, layer, syntax_check, subprocess_kwargs
            )

            @add_finalizer
            def clean_up():
                # this closes the web socket so we don't get a
                #   ResourceWarning: unclosed <ssl.SSLSocket ... >
                # warning on every test
                # based on https://github.com/boto/botocore/pull/1810
                # (if that's ever merged, this can just become client.close())
                session = client._endpoint.http_session
                managers = [session._manager] + list(session._proxy_managers.values())
                for manager in managers:
                    manager.clear()

        layers = []
        environment = {}
        handler = initial_handler or "test_lambda.test_handler"

        if layer is not None:
            with open(
                os.path.join(base_dir, "lambda-layer-package.zip"), "rb"
            ) as lambda_layer_zip:
                response = client.publish_layer_version(
                    LayerName="python-serverless-sdk-test",
                    Description="Created as part of testsuite for getsentry/sentry-python",
                    Content={"ZipFile": lambda_layer_zip.read()},
                )

            layers = [response["LayerVersionArn"]]
            handler = (
                "sentry_sdk.integrations.init_serverless_sdk.sentry_lambda_handler"
            )
            environment = {
                "Variables": {
                    "SENTRY_INITIAL_HANDLER": initial_handler
                    or "test_lambda.test_handler",
                    "SENTRY_DSN": "https://123abc@example.com/123",
                    "SENTRY_TRACES_SAMPLE_RATE": "1.0",
                }
            }

        try:
            with open(
                os.path.join(base_dir, "lambda-function-package.zip"), "rb"
            ) as lambda_function_zip:
                client.create_function(
                    Description="Created as part of testsuite for getsentry/sentry-python",
                    FunctionName=full_fn_name,
                    Runtime=runtime,
                    Timeout=timeout,
                    Role=AWS_LAMBDA_EXECUTION_ROLE_ARN,
                    Handler=handler,
                    Code={"ZipFile": lambda_function_zip.read()},
                    Environment=environment,
                    Layers=layers,
                )

                waiter = client.get_waiter("function_active_v2")
                waiter.wait(FunctionName=full_fn_name)
        except client.exceptions.ResourceConflictException:
            print(
                "Lambda function already exists, this is fine, we will just invoke it."
            )

    response = client.invoke(
        FunctionName=full_fn_name,
        InvocationType="RequestResponse",
        LogType="Tail",
        Payload=payload,
    )

    assert 200 <= response["StatusCode"] < 300, response
    return response


# This is for inspecting new Python runtime environments in AWS Lambda
# If you need to debug a new runtime, use this REPL to run arbitrary Python or bash commands
# in that runtime in a Lambda function:
#
#    pip3 install click
#    python3 tests/integrations/aws_lambda/client.py --runtime=python4.0
#


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
        "--runtime", required=True, help="name of the runtime to use, eg python3.11"
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
                subprocess_kwargs=(
                    {
                        "stdout": subprocess.DEVNULL,
                        "stderr": subprocess.DEVNULL,
                    }
                    if not verbose
                    else {}
                ),
            )

            for line in base64.b64decode(response["LogResult"]).splitlines():
                print(line.decode("utf8"))

            for f in cleanup:
                f()

            cleanup = []

    if __name__ == "__main__":
        repl()
