import os

import sentry_sdk
from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration

# otherwise SAM will call its built-in metadata endpoint/token.
os.environ["AWS_LAMBDA_METADATA_API"] = os.environ[
    "SENTRY_TEST_AWS_LAMBDA_METADATA_API"
]
os.environ["AWS_LAMBDA_METADATA_TOKEN"] = os.environ[
    "SENTRY_TEST_AWS_LAMBDA_METADATA_TOKEN"
]

sentry_sdk.init(
    dsn=os.environ.get("SENTRY_DSN"),
    traces_sample_rate=1.0,
    integrations=[AwsLambdaIntegration()],
    trace_lifecycle="stream",
    data_collection={
        "url_query_params": {
            "mode": "denylist",
            "terms": ["tracking"],
        }
    },
)


def handler(event, context):
    return {"event": event}
