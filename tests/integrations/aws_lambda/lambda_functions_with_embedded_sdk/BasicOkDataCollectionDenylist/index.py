import os

import sentry_sdk
from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration

sentry_sdk.init(
    dsn=os.environ.get("SENTRY_DSN"),
    traces_sample_rate=1.0,
    integrations=[AwsLambdaIntegration()],
    _experiments={
        "data_collection": {
            "http_headers": {
                "request": {
                    "mode": "denylist",
                    # Custom terms deny otherwise non-sensitive headers on top
                    # of the built-in sensitive denylist.
                    "terms": ["x-forwarded", "user-agent"],
                }
            }
        }
    },
)


def handler(event, context):
    return {"event": event}
