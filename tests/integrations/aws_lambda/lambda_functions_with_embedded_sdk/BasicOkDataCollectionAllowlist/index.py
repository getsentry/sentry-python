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
                    "mode": "allowlist",
                    # "authorization" is allowlisted on purpose to show that an
                    # allowlist entry cannot override the built-in sensitive
                    # denylist.
                    "terms": ["user-agent", "x-allow-me", "authorization"],
                }
            }
        }
    },
)


def handler(event, context):
    return {"event": event}
