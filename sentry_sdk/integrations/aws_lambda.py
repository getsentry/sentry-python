import sys

from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk._compat import reraise
from sentry_sdk.utils import (
    AnnotatedValue,
    capture_internal_exceptions,
    event_from_exception,
    logger,
)
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations._wsgi_common import _filter_headers


def _wrap_handler(handler):
    def sentry_handler(event, context, *args, **kwargs):
        hub = Hub.current
        integration = hub.get_integration(AwsLambdaIntegration)
        if integration is None:
            return handler(event, context, *args, **kwargs)

        with hub.push_scope() as scope:
            with capture_internal_exceptions():
                scope.clear_breadcrumbs()
                scope.transaction = context.function_name
                scope.add_event_processor(_make_request_event_processor(event, context))

            try:
                return handler(event, context, *args, **kwargs)
            except Exception:
                exc_info = sys.exc_info()
                event, hint = event_from_exception(
                    exc_info,
                    client_options=hub.client.options,
                    mechanism={"type": "aws_lambda", "handled": False},
                )
                hub.capture_event(event, hint=hint)
                reraise(*exc_info)

    return sentry_handler


def _drain_queue():
    with capture_internal_exceptions():
        hub = Hub.current
        integration = hub.get_integration(AwsLambdaIntegration)
        if integration is not None:
            # Flush out the event queue before AWS kills the
            # process.
            hub.client.flush()


class AwsLambdaIntegration(Integration):
    identifier = "aws_lambda"

    @staticmethod
    def setup_once():
        import __main__ as lambda_bootstrap  # type: ignore

        pre_37 = True  # Python 3.6 or 2.7

        if not hasattr(lambda_bootstrap, "handle_http_request"):
            try:
                import bootstrap as lambda_bootstrap  # type: ignore

                pre_37 = False  # Python 3.7
            except ImportError:
                pass

        if not hasattr(lambda_bootstrap, "handle_event_request"):
            logger.warning(
                "Not running in AWS Lambda environment, "
                "AwsLambdaIntegration disabled"
            )
            return

        if pre_37:
            old_handle_event_request = lambda_bootstrap.handle_event_request

            def sentry_handle_event_request(request_handler, *args, **kwargs):
                request_handler = _wrap_handler(request_handler)
                return old_handle_event_request(request_handler, *args, **kwargs)

            lambda_bootstrap.handle_event_request = sentry_handle_event_request

            old_handle_http_request = lambda_bootstrap.handle_http_request

            def sentry_handle_http_request(request_handler, *args, **kwargs):
                request_handler = _wrap_handler(request_handler)
                return old_handle_http_request(request_handler, *args, **kwargs)

            lambda_bootstrap.handle_http_request = sentry_handle_http_request

            # Patch to_json to drain the queue. This should work even when the
            # SDK is initialized inside of the handler

            old_to_json = lambda_bootstrap.to_json

            def sentry_to_json(*args, **kwargs):
                _drain_queue()
                return old_to_json(*args, **kwargs)

            lambda_bootstrap.to_json = sentry_to_json
        else:
            old_handle_event_request = lambda_bootstrap.handle_event_request

            def sentry_handle_event_request(  # type: ignore
                lambda_runtime_client, request_handler, *args, **kwargs
            ):
                request_handler = _wrap_handler(request_handler)
                return old_handle_event_request(
                    lambda_runtime_client, request_handler, *args, **kwargs
                )

            lambda_bootstrap.handle_event_request = sentry_handle_event_request

            # Patch the runtime client to drain the queue. This should work
            # even when the SDK is initialized inside of the handler

            def _wrap_post_function(f):
                def inner(*args, **kwargs):
                    _drain_queue()
                    return f(*args, **kwargs)

                return inner

            lambda_bootstrap.LambdaRuntimeClient.post_invocation_result = _wrap_post_function(
                lambda_bootstrap.LambdaRuntimeClient.post_invocation_result
            )
            lambda_bootstrap.LambdaRuntimeClient.post_invocation_error = _wrap_post_function(
                lambda_bootstrap.LambdaRuntimeClient.post_invocation_error
            )


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

        if "httpMethod" in aws_event:
            request["method"] = aws_event["httpMethod"]

        request["url"] = _get_url(aws_event, aws_context)

        if "queryStringParameters" in aws_event:
            request["query_string"] = aws_event["queryStringParameters"]

        if "headers" in aws_event:
            request["headers"] = _filter_headers(aws_event["headers"])

        if aws_event.get("body", None):
            # Unfortunately couldn't find a way to get structured body from AWS
            # event. Meaning every body is unstructured to us.
            request["data"] = AnnotatedValue("", {"rem": [["!raw", "x", 0, 0]]})

        if _should_send_default_pii():
            user_info = event.setdefault("user", {})

            id = aws_event.get("identity", {}).get("userArn")
            if id is not None:
                user_info["id"] = id

            ip = aws_event.get("identity", {}).get("sourceIp")
            if ip is not None:
                user_info["ip_address"] = ip

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
