import json
import os

import sentry_sdk
from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration
from sentry_sdk.integrations.aws_lambda._runtime import get_lambda_bootstrap

sentry_sdk.init(
    dsn=os.environ.get("SENTRY_DSN"),
    traces_sample_rate=1.0,
    integrations=[AwsLambdaIntegration()],
)

# `sam local` drops `ClientContext`; pass the event's `client_context` to the runtime
# instead, like lambda does for a synchronous `Invoke`.
bootstrap = get_lambda_bootstrap()
handle_event_request = bootstrap.handle_event_request


def handle_event_request_with_client_context(
    lambda_runtime_client,
    request_handler,
    invoke_id,
    event_body,
    content_type,
    client_context_json,
    *args,
):
    client_context_json = json.dumps(json.loads(event_body)["client_context"])
    return handle_event_request(
        lambda_runtime_client,
        request_handler,
        invoke_id,
        event_body,
        content_type,
        client_context_json,
        *args,
    )


bootstrap.handle_event_request = handle_event_request_with_client_context


def handler(event, context):
    return {"event": event}
