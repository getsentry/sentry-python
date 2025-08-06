"""
For manual instrumentation,
The Handler function string of an aws lambda function should be added as an
environment variable with a key of 'SENTRY_INITIAL_HANDLER' along with the 'DSN'
Then the Handler function sstring should be replaced with
'sentry_sdk.integrations.init_serverless_sdk.sentry_lambda_handler'
"""

import os
import sys
import re
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration


# Configure Sentry SDK
sentry_sdk.init(
    dsn=os.environ["SENTRY_DSN"],
    integrations=[AwsLambdaIntegration(timeout_warning=True)],
    traces_sample_rate=float(os.environ["SENTRY_TRACES_SAMPLE_RATE"]),
)


class AWSLambdaModuleLoader:
    DIR_PATH_REGEX = r"^(.+)\/([^\/]+)$"

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
        py_version = sys.version_info

        if re.match(self.DIR_PATH_REGEX, module_path):
            # With a path like -> `scheduler/scheduler/event`
            # `module_name` is `event`, and `module_file_path` is `scheduler/scheduler/event.py`
            module_name = module_path.split(os.path.sep)[-1]
            module_file_path = module_path + ".py"

            # Supported python versions are 3.7, 3.8
            if py_version >= (3, 7):
                import importlib.util

                spec = importlib.util.spec_from_file_location(
                    module_name, module_file_path
                )
                self.lambda_function_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(self.lambda_function_module)
            else:
                raise ValueError("Python version %s is not supported." % py_version)
        else:
            import importlib

            self.lambda_function_module = importlib.import_module(module_path)

    def get_lambda_handler(self):
        return getattr(self.lambda_function_module, self.handler_name)


def sentry_lambda_handler(event: Any, context: Any) -> None:
    """
    Handler function that invokes a lambda handler which path is defined in
    environment variables as "SENTRY_INITIAL_HANDLER"
    """
    module_loader = AWSLambdaModuleLoader(os.environ["SENTRY_INITIAL_HANDLER"])
    return module_loader.get_lambda_handler()(event, context)
