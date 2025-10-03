import logging
import os
import sentry_sdk

from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration
from sentry_sdk.integrations.logging import LoggingIntegration


def lambda_handler(event, context):
    sentry_sdk.init(
        dsn=os.environ.get("SENTRY_DSN"),
        attach_stacktrace=True,
        integrations=[
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            AwsLambdaIntegration(timeout_warning=True),
        ],
        traces_sample_rate=1.0,
        debug=True,
    )

    try:
        my_dict = {"a": "test"}
        _ = my_dict["b"]  # This should raise exception
    except:
        logging.exception("Key Does not Exists")
        raise
