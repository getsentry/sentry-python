import logging
import sentry_sdk

from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

def lambda_handler(event, context):
    sentry_sdk.init(
        dsn="https://d655584d05f14c58b86e9034aab6817f@o447951.ingest.us.sentry.io/5461230",
        attach_stacktrace=True,
        integrations=[
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            AwsLambdaIntegration(timeout_warning=True)
        ],
        traces_sample_rate=1.0,
        debug=True,
    )

    try:
        my_dict = {"a" : "test"}
        value = my_dict["b"] # This should raise exception
    except:
        logging.exception("Key Does not Exists")
        raise
