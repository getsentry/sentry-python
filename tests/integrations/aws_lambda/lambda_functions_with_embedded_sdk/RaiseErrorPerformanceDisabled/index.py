import os
import sentry_sdk_alpha
from sentry_sdk_alpha.integrations.aws_lambda import AwsLambdaIntegration


sentry_sdk_alpha.init(
    dsn=os.environ.get("SENTRY_DSN"),
    traces_sample_rate=None,  # this is the default, just added for clarity
    integrations=[AwsLambdaIntegration()],
)


def handler(event, context):
    raise Exception("Oh!")
