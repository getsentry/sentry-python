"""
For manual instrumentation,
The Handler function string of an aws lambda function should be added as an
environment variable with a key of 'SENTRY_INITIAL_HANDLER' along with the 'DSN'
Then the Handler function sstring should be replaced with
'sentry_sdk.integrations.init_serverless_sdk.sentry_lambda_handler'
"""

import importlib
import os
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration

if TYPE_CHECKING:
    from typing import Any


# Configure Sentry SDK
sentry_sdk.init(
    dsn=os.environ["SENTRY_DSN"],
    integrations=[AwsLambdaIntegration(timeout_warning=True)],
    traces_sample_rate=float(os.environ["SENTRY_TRACES_SAMPLE_RATE"]),
)


class AWSLambdaModuleLoader:
    def __init__(self, sentry_initial_handler):
        try:
            module_path, self.handler_name = sentry_initial_handler.rsplit(".", 1)
        except ValueError:
            raise ValueError("Incorrect AWS Handler path (Not a path)")

        self.extract_and_load_lambda_function_module(module_path)

    def extract_and_load_lambda_function_module(self, module_path):
        """
        Method that extracts and loads lambda function module from module_path
        """
        # like the Lambda runtime: `dir/file` is the module `dir.file`, which `sys.modules`
        # keeps, so its code only runs on the first invocation.
        # https://github.com/aws/aws-lambda-python-runtime-interface-client/blob/4.2.2/awslambdaric/bootstrap.py#L53
        self.lambda_function_module = importlib.import_module(
            module_path.replace("/", ".")
        )

    def get_lambda_handler(self):
        return getattr(self.lambda_function_module, self.handler_name)


def sentry_lambda_handler(event: "Any", context: "Any") -> "Any":
    """
    Handler function that invokes a lambda handler which path is defined in
    environment variables as "SENTRY_INITIAL_HANDLER"
    """
    module_loader = AWSLambdaModuleLoader(os.environ["SENTRY_INITIAL_HANDLER"])
    return module_loader.get_lambda_handler()(event, context)
