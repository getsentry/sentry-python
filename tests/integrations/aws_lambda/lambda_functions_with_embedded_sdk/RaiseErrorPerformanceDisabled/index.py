import os
import sentry_sdk
from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration


sentry_sdk.init(
    dsn=os.environ.get("SENTRY_DSN"),
    traces_sample_rate=None,  # this is the default, just added for clarity
    integrations=[AwsLambdaIntegration()],
    debug=True,
)


def handler(event, context):
    raise Exception("Oh!")
