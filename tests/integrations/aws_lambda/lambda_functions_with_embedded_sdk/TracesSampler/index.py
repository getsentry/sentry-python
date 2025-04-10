import json
import os
import sentry_sdk
from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration

# Global variables to store sampling context for verification
sampling_context_data = None


def trace_sampler(sampling_context):
    # Store the sampling context for verification
    global sampling_context_data
    sampling_context_data = sampling_context

    return 1.0  # Always sample


sentry_sdk.init(
    dsn=os.environ.get("SENTRY_DSN"),
    traces_sample_rate=1.0,
    traces_sampler=trace_sampler,
    integrations=[AwsLambdaIntegration()],
)


def handler(event, context):
    # Return the sampling context data for verification
    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "message": "Hello from Lambda with embedded Sentry SDK!",
                "event": event,
                "sampling_context_data": sampling_context_data,
            }
        ),
    }
