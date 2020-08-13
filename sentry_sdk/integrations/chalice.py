import sys
import traceback
from datetime import datetime

import chalice
from chalice import Chalice, ChaliceViewError, Response
from chalice.app import EventSourceHandler as ChaliceEventSourceHandler
from sentry_sdk._types import MYPY
from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations._wsgi_common import _filter_headers
from sentry_sdk.integrations.aws_lambda import _get_cloudwatch_logs_url
from sentry_sdk.utils import (
    AnnotatedValue,
    capture_internal_exceptions,
    event_from_exception,
)

if MYPY:
    from typing import Any, Optional

    from sentry_sdk._types import Event, EventProcessor, Hint


class EventSourceHandler(ChaliceEventSourceHandler):

    def __call__(self, event, context):
        hub = Hub.current
        client = hub.client

        with hub.push_scope() as scope:
            try:
                event_obj = self.event_class(event, context)
                return self.func(event_obj)
            except Exception:
                scope.add_event_processor(
                    _make_request_event_processor(
                        event, context
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
        chalice.app.EventSourceHandler = EventSourceHandler


def _make_request_event_processor(current_request, lambda_context):
    # type: (Any, Any) -> EventProcessor
    start_time = datetime.now()

    def event_processor(event, hint, start_time=start_time):
        # type: (Event, Hint, datetime) -> Optional[Event]

        extra = event.setdefault("extra", {})

        extra["Chalice-lambda"] = {
            "function_name": lambda_context.function_name,
            "function_version": lambda_context.function_version,
            "Lambda ARN": lambda_context.invoked_function_arn,
            "aws_request_id": lambda_context.aws_request_id,
        }

        extra["cloudwatch-info"] = {
            "url": _get_cloudwatch_logs_url(lambda_context, start_time),
            "log_group": lambda_context.log_group_name,
            "log_stream": lambda_context.log_stream_name,
        }

        request_info = event.get("request", {})

        request_info["method"] = current_request.context["httpMethod"]

        request_info["query_string"] = current_request.query_params

        request_info["headers"] = _filter_headers(current_request.headers)

        if current_request._body is None:
            request_info["data"] = AnnotatedValue(
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

        event["request"] = request_info

        return event

    return event_processor
