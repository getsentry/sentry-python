import os

import sentry_sdk
from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration

# the alias is only in the name to get a qualified invoked ARN
os.environ["AWS_LAMBDA_FUNCTION_NAME"] = os.environ["AWS_LAMBDA_FUNCTION_NAME"].split(
    ":"
)[0]

sentry_sdk.init(
    dsn=os.environ.get("SENTRY_DSN"),
    traces_sample_rate=1.0,
    integrations=[AwsLambdaIntegration()],
)


def handler(event, context):
    raise Exception("Oh!")
