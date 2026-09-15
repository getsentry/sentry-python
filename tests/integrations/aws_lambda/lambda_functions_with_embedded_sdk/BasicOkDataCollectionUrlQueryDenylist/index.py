import os

import sentry_sdk
from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration

sentry_sdk.init(
    dsn=os.environ.get("SENTRY_DSN"),
    traces_sample_rate=1.0,
    integrations=[AwsLambdaIntegration()],
    _experiments={
        "data_collection": {
            "url_query_params": {
                "mode": "denylist",
                # Custom terms deny otherwise non-sensitive query params on top
                # of the built-in sensitive denylist.
                "terms": ["tracking"],
            }
        }
    },
)


def handler(event, context):
    return {"event": event}
