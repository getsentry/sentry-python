"""
For manual instrumentation,
The Handler function string of an aws lambda function should be added as an
environment variable with a key of 'INITIAL_HANDLER' along with the 'DSN'
Then the Handler function sstring should be replaced with
'sentry_sdk.integrations.aws_lambda.init_serverless_sdk.sentry_lambda_handler'
"""
import os

import sentry_sdk
from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration

# Configure Sentry SDK
sentry_sdk.init(
    dsn=os.environ["DSN"],
    integrations=[AwsLambdaIntegration(timeout_warning=True)],
)


def sentry_lambda_handler(event, context):
    """
    Handler function that invokes a lambda handler which path is defined in
    environment vairables as "INITIAL_HANDLER"
    """
    try:
        module_name, handler_name = os.environ["INITIAL_HANDLER"].rsplit(".", 1)
    except ValueError:
        raise ValueError("Incorrect AWS Handler path (Not a path)")
    lambda_function = __import__(module_name)
    lambda_handler = getattr(lambda_function, handler_name)
    lambda_handler(event, context)
