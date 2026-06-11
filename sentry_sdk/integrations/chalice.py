import sys
from functools import wraps

import sentry_sdk
import sentry_sdk.traces
from sentry_sdk.consts import OP
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations._wsgi_common import _filter_headers
from sentry_sdk.integrations.aws_lambda import _make_request_event_processor
from sentry_sdk.integrations.cloud_resource_context import (
    CLOUD_PLATFORM,
    CLOUD_PROVIDER,
)
from sentry_sdk.traces import (
    SegmentSource,
    SpanStatus,
    StreamedSpan,
    get_current_span,
)
from sentry_sdk.tracing import TransactionSource
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
    parse_version,
    reraise,
)

try:
    import chalice  # type: ignore
    from chalice import Chalice, ChaliceViewError
    from chalice import __version__ as CHALICE_VERSION
    from chalice.app import (  # type: ignore
        EventSourceHandler as ChaliceEventSourceHandler,
    )
except ImportError:
    raise DidNotEnable("Chalice is not installed")

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, TypeVar

    F = TypeVar("F", bound=Callable[..., Any])


class EventSourceHandler(ChaliceEventSourceHandler):  # type: ignore
    def __call__(self, event: "Any", context: "Any") -> "Any":
        client = sentry_sdk.get_client()

        with sentry_sdk.isolation_scope() as scope:
            with capture_internal_exceptions():
                configured_time = context.get_remaining_time_in_millis()
                scope.add_event_processor(
                    _make_request_event_processor(event, context, configured_time)
                )

            try:
                return ChaliceEventSourceHandler.__call__(self, event, context)
            except Exception:
                exc_info = sys.exc_info()
                sentry_event, hint = event_from_exception(
                    exc_info,
                    client_options=client.options,
                    mechanism={"type": "chalice", "handled": False},
                )
                sentry_sdk.capture_event(sentry_event, hint=hint)
                client.flush()
                reraise(*exc_info)


def _get_view_function_response(
    app: "Any", view_function: "F", function_args: "Any"
) -> "F":
    @wraps(view_function)
    def wrapped_view_function(**function_args: "Any") -> "Any":
        client = sentry_sdk.get_client()
        with sentry_sdk.isolation_scope() as scope:
            with capture_internal_exceptions():
                configured_time = app.lambda_context.get_remaining_time_in_millis()
                scope.add_event_processor(
                    _make_request_event_processor(
                        app.current_request.to_dict(),
                        app.lambda_context,
                        configured_time,
                    )
                )

            if has_span_streaming_enabled(client.options):
                current_span = get_current_span()
                segment = None
                if type(current_span) is StreamedSpan:
                    # A segment already exists (created by the AWS Lambda
                    # integration), so decorate it with Chalice attributes
                    # The AWS Lambda integration owns the span lifecycle
                    # (end + flush), but Chalice converts unhandled view exceptions
                    # into 500 responses, so the error must be captured here.
                    aws_context = app.lambda_context
                    request_dict = app.current_request.to_dict()
                    headers = request_dict.get("headers", {})

                    header_attrs: "Dict[str, Any]" = {}
                    for header, value in _filter_headers(
                        headers, use_annotated_value=False
                    ).items():
                        header_attrs[f"http.request.header.{header.lower()}"] = value

                    additional_attrs: "Dict[str, Any]" = {}
                    if "method" in request_dict:
                        additional_attrs["http.request.method"] = request_dict["method"]

                    attributes = {
                        **_get_lambda_span_attributes(aws_context),
                        **header_attrs,
                        **additional_attrs,
                    }

                    segment = current_span._segment
                    segment.set_attributes(attributes)

                try:
                    return view_function(**function_args)
                except Exception as exc:
                    if isinstance(exc, ChaliceViewError):
                        raise
                    exc_info = sys.exc_info()
                    if segment:
                        segment.status = SpanStatus.ERROR.value
                    sentry_event, hint = event_from_exception(
                        exc_info,
                        client_options=client.options,
                        mechanism={"type": "chalice", "handled": False},
                    )
                    sentry_sdk.capture_event(sentry_event, hint=hint)
                    raise
            else:
                scope.set_transaction_name(
                    app.lambda_context.function_name,
                    source=TransactionSource.COMPONENT,
                )
                try:
                    return view_function(**function_args)
                except Exception as exc:
                    if isinstance(exc, ChaliceViewError):
                        raise
                    exc_info = sys.exc_info()
                    sentry_event, hint = event_from_exception(
                        exc_info,
                        client_options=client.options,
                        mechanism={"type": "chalice", "handled": False},
                    )
                    sentry_sdk.capture_event(sentry_event, hint=hint)
                    client.flush()
                    raise

    return wrapped_view_function  # type: ignore


class ChaliceIntegration(Integration):
    identifier = "chalice"
    origin = f"auto.function.{identifier}"

    @staticmethod
    def setup_once() -> None:
        version = parse_version(CHALICE_VERSION)

        if version is None:
            raise DidNotEnable("Unparsable Chalice version: {}".format(CHALICE_VERSION))

        if version < (1, 20):
            old_get_view_function_response = Chalice._get_view_function_response
        else:
            from chalice.app import RestAPIEventHandler

            old_get_view_function_response = (
                RestAPIEventHandler._get_view_function_response
            )

        def sentry_event_response(
            app: "Any", view_function: "F", function_args: "Dict[str, Any]"
        ) -> "Any":
            wrapped_view_function = _get_view_function_response(
                app, view_function, function_args
            )

            return old_get_view_function_response(
                app, wrapped_view_function, function_args
            )

        if version < (1, 20):
            Chalice._get_view_function_response = sentry_event_response
        else:
            RestAPIEventHandler._get_view_function_response = sentry_event_response
        # for everything else (like events)
        chalice.app.EventSourceHandler = EventSourceHandler


def _get_lambda_span_attributes(aws_context: "Any") -> "Dict[str, Any]":
    invoked_arn = aws_context.invoked_function_arn
    split_invoked_arn = invoked_arn.split(":")
    aws_region = split_invoked_arn[3] if len(split_invoked_arn) > 3 else "unknown"

    return {
        "sentry.op": OP.FUNCTION_AWS,
        "sentry.origin": ChaliceIntegration.origin,
        "sentry.span.source": SegmentSource.COMPONENT,
        "cloud.platform": CLOUD_PLATFORM.AWS_LAMBDA,
        "cloud.provider": CLOUD_PROVIDER.AWS,
        "faas.name": aws_context.function_name,
        "cloud.region": aws_region,
        "cloud.resource_id": invoked_arn,
        "aws.lambda.invoked_arn": invoked_arn,
        "faas.invocation_id": aws_context.aws_request_id,
        "faas.version": aws_context.function_version,
        "aws.log.group.names": [aws_context.log_group_name],
        "aws.log.stream.names": [aws_context.log_stream_name],
    }
