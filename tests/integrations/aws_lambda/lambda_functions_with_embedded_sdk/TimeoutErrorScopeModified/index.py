import os
import time

import sentry_sdk
from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration

sentry_sdk.init(
    dsn=os.environ.get("SENTRY_DSN"),
    traces_sample_rate=1.0,
    integrations=[AwsLambdaIntegration(timeout_warning=True)],
)


def handler(event, context):
    sentry_sdk.set_tag("custom_tag", "custom_value")
    time.sleep(15)
    return {
        "event": event,
    }
