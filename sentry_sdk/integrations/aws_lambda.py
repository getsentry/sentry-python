import sys

from sentry_sdk import configure_scope
from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk._compat import reraise
from sentry_sdk.utils import (
    AnnotatedValue,
    capture_internal_exceptions,
    event_from_exception,
)
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations._wsgi import _filter_headers

import __main__ as lambda_bootstrap


class AwsLambdaIntegration(Integration):
    identifier = "aws_lambda"

    def install(self):
        old_make_final_handler = lambda_bootstrap.make_final_handler

        def sentry_make_final_handler(*args, **kwargs):
            handler = old_make_final_handler(*args, **kwargs)

            def sentry_handler(event, context, *args, **kwargs):
                hub = Hub.current

                with hub.push_scope():
                    with capture_internal_exceptions():
                        with configure_scope() as scope:
                            scope.transaction = context.function_name
                            scope.add_event_processor(
                                _make_request_event_processor(event, context)
                            )

                    try:
                        return handler(event, context, *args, **kwargs)
                    except Exception:
                        exc_info = sys.exc_info()
                        event, hint = event_from_exception(
                            exc_info,
                            with_locals=hub.client.options["with_locals"],
                            mechanism={"type": "aws_lambda", "handled": False},
                        )

                        hub.capture_event(event, hint=hint)
                        reraise(*exc_info)
                    finally:
                        client = hub.client
                        # Flush out the event queue before AWS kills the
                        # process. This is not threadsafe.
                        if client is not None:
                            # make new transport with empty queue
                            new_transport = client.transport.copy()
                            client.close()
                            client.transport = new_transport

            return sentry_handler

        lambda_bootstrap.make_final_handler = sentry_make_final_handler


def _make_request_event_processor(aws_event, aws_context):
    def event_processor(event, hint):
        extra = event.setdefault("extra", {})
        extra["lambda"] = {
            "remaining_time_in_millis": aws_context.get_remaining_time_in_millis(),
            "function_name": aws_context.function_name,
            "function_version": aws_context.function_version,
            "invoked_function_arn": aws_context.invoked_function_arn,
            "aws_request_id": aws_context.aws_request_id,
        }

        request = event.setdefault("request", {})

        if "httpMethod" in aws_event and "method" not in request:
            request["method"] = aws_event["httpMethod"]
        if "url" not in request:
            request["url"] = _get_url(aws_event, aws_context)
        if "queryStringParameters" in aws_event and "query_string" not in request:
            request["query_string"] = aws_event["queryStringParameters"]
        if "headers" in aws_event and "headers" not in request:
            request["headers"] = _filter_headers(aws_event["headers"])
        if aws_event.get("body", None):
            # Unfortunately couldn't find a way to get structured body from AWS
            # event. Meaning every body is unstructured to us.
            request["data"] = AnnotatedValue("", {"rem": [["!raw", "x", 0, 0]]})

        if _should_send_default_pii():
            user_info = event.setdefault("user", {})
            if "id" not in user_info:
                user_info["id"] = aws_event.get("identity", {}).get("userArn")
            if "ip_address" not in user_info:
                user_info["ip_address"] = aws_event.get("identity", {}).get("sourceIp")

        return event

    return event_processor


def _get_url(event, context):
    path = event.get("path", None)
    headers = event.get("headers", {})
    host = headers.get("Host", None)
    proto = headers.get("X-Forwarded-Proto", None)
    if proto and host and path:
        return "{}://{}{}".format(proto, host, path)
    return "awslambda:///{}".format(context.function_name)
