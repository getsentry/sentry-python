import sys
import traceback

import chalice
from chalice import Chalice, ChaliceViewError, Response
from chalice.app import EventSourceHandler as ChaliceEventSourceHandler
from sentry_sdk._compat import reraise
from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.aws_lambda import _make_request_event_processor
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
)


class EventSourceHandler(ChaliceEventSourceHandler):
    def __call__(self, event, context):
        hub = Hub.current
        client = hub.client

        with hub.push_scope() as scope:
            try:
                event_obj = self.event_class(event, context)
                return self.func(event_obj)
            except Exception:
                configured_time = context.get_remaining_time_in_millis()
                scope.add_event_processor(
                    _make_request_event_processor(event, context, configured_time)
                )
                exc_info = sys.exc_info()
                event, hint = event_from_exception(
                    exc_info,
                    client_options=client.options,
                    mechanism={"type": "chalice", "handled": False},
                )
                hub.capture_event(event, hint=hint)
                hub.flush()
                reraise(*exc_info)


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
                body={"Code": e.__class__.__name__, "Message": str(e)},
                status_code=e.STATUS_CODE,
            )
            hub.flush()
        except Exception:
            with capture_internal_exceptions():
                configured_time = app.lambda_context.get_remaining_time_in_millis()
                scope.transaction = app.lambda_context.function_name
                scope.add_event_processor(
                    _make_request_event_processor(
                        app.current_request.to_dict(),
                        app.lambda_context,
                        configured_time,
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
            app.log.error("Caught exception for %s", view_function, exc_info=True)
            if app.debug:
                # If the user has turned on debug mode,
                # we'll let the original exception propagate so
                # they get more information about what went wrong.
                stack_trace = "".join(traceback.format_exc())
                body = stack_trace
                headers["Content-Type"] = "text/plain"
            else:
                body = {
                    "Code": "InternalServerError",
                    "Message": "An internal server error occurred.",
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
