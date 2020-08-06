import sys
import traceback
from datetime import datetime, timedelta
from os import environ

from chalice import Chalice, ChaliceViewError, Response
from sentry_sdk._types import MYPY
from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations._wsgi_common import _filter_headers
from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration
from sentry_sdk.utils import (
    AnnotatedValue,
    capture_internal_exceptions,
    event_from_exception,
)

if MYPY:
    from typing import Any, Optional

    from sentry_sdk._types import Event, EventProcessor, Hint


def _get_view_function_response(app, view_function, function_args):
    hub = Hub.current
    client = hub.client

    with hub.push_scope() as scope:
        try:
            response = view_function(**function_args)
            if not isinstance(response, Response):
                response = Response(body=response)
            app._validate_response(response)
        except ChaliceViewError as e:
            # Any chalice view error should propagate.  These
            # get mapped to various HTTP status codes in API Gateway.
            response = Response(
                body={'Code': e.__class__.__name__, 'Message': str(e)},
                status_code=e.STATUS_CODE,
            )
            hub.flush()
        except Exception:
            with capture_internal_exceptions():
                scope.transaction = app.lambda_context.function_name
                scope.add_event_processor(
                    _make_request_event_processor(
                        app.current_request, app.lambda_context
                    )
                )
            exc_info = sys.exc_info()
            event, hint = event_from_exception(
                exc_info,
                client_options=client.options,
                mechanism={"type": "chalice", "handled": False},
            )
            hub.capture_event(event, hint=hint)
            hub.flush()
            headers = {}
            app.log.error(
                "Caught exception for %s", view_function, exc_info=True
            )
            if app.debug:
                # If the user has turned on debug mode,
                # we'll let the original exception propagate so
                # they get more information about what went wrong.
                stack_trace = ''.join(traceback.format_exc())
                body = stack_trace
                headers['Content-Type'] = 'text/plain'
            else:
                body = {
                    'Code': 'InternalServerError',
                    'Message': 'An internal server error occurred.',
                }
            response = Response(body=body, headers=headers, status_code=500)
    return response


class ChaliceIntegration(Integration):
    identifier = "chalice"

    @staticmethod
    def setup_once():
        # for @app.route()
        Chalice._get_view_function_response = _get_view_function_response
        # for everything else (like events)
        AwsLambdaIntegration.setup_once()


def _make_request_event_processor(current_request, lambda_context):
    # type: (Any, Any) -> EventProcessor
    start_time = datetime.now()

    def event_processor(event, hint, start_time=start_time):
        # type: (Event, Hint, datetime) -> Optional[Event]
        extra = event.setdefault("extra", {})
        extra["lambda"] = {
            "function_name": lambda_context.function_name,
            "function_version": lambda_context.function_version,
            "invoked_function_arn": lambda_context.invoked_function_arn,
            "aws_request_id": lambda_context.aws_request_id,
        }

        extra["cloudwatch logs"] = {
            "url": _get_cloudwatch_logs_url(lambda_context, start_time),
            "log_group": lambda_context.log_group_name,
            "log_stream": lambda_context.log_stream_name,
        }

        request = event.get("request", {})
        if "httpMethod" in current_request.context:
            request["method"] = current_request.context["httpMethod"]

        request["url"] = _get_url(current_request, lambda_context)

        if "query_params" in current_request.to_dict():
            request["query_string"] = current_request.query_params

        if "headers" in current_request.to_dict():
            request["headers"] = _filter_headers(current_request.headers)

        if current_request._body is None:
            # Unfortunately couldn't find a way to get structured body from AWS
            # event. Meaning every body is unstructured to us.
            request["data"] = AnnotatedValue(
                "", {"rem": [["!raw", "x", 0, 0]]}
            )

        if _should_send_default_pii():
            user_info = event.setdefault("user", {})

            id = current_request.context["identity"]["userArn"]
            if id is not None:
                user_info.setdefault("id", id)

            ip = current_request.context["identity"]["sourceIp"]
            if ip is not None:
                user_info.setdefault("ip_address", ip)

        event["request"] = request

        return event

    return event_processor


def _get_url(event, context):
    # type: (Any, Any) -> str
    path = event.context["path"]
    headers = event.headers
    host = headers.get("Host", None)
    proto = headers.get("X-Forwarded-Proto", None)
    if proto and host and path:
        return "{}://{}{}".format(proto, host, path)
    return "awslambda:///{}".format(context.function_name)


def _get_cloudwatch_logs_url(context, start_time):
    # type: (Any, datetime) -> str
    """
    Generates a CloudWatchLogs console URL based on the context object
    Arguments:
        context {Any} -- context from lambda handler
    Returns:
        str -- AWS Console URL to logs.
    """
    formatstring = "%Y-%m-%dT%H:%M:%S"

    url = (
        "https://console.aws.amazon.com/cloudwatch/home?region={region}"
        "#logEventViewer:group={log_group};stream={log_stream}"
        ";start={start_time};end={end_time}"
    ).format(
        region=environ.get("AWS_REGION"),
        log_group=context.log_group_name,
        log_stream=context.log_stream_name,
        start_time=(start_time - timedelta(seconds=1)).strftime(formatstring),
        end_time=(datetime.now() + timedelta(seconds=2)).strftime(
            formatstring
        ),
    )

    return url
