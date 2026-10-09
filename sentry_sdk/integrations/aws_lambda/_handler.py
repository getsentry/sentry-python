import functools
import sys
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import sentry_sdk
from sentry_sdk.api import continue_trace
from sentry_sdk.consts import OP
from sentry_sdk.data_collection import _apply_key_value_collection_filtering
from sentry_sdk.integrations._wsgi_common import _filter_headers
from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration
from sentry_sdk.integrations.aws_lambda._request import (
    _get_user_from_event,
    _make_request_event_processor,
)
from sentry_sdk.integrations.aws_lambda.consts import (
    MILLIS_TO_SECONDS,
    ORIGIN,
    TIMEOUT_WARNING_BUFFER,
)
from sentry_sdk.integrations.cloud_resource_context import (
    CLOUD_PLATFORM,
    CLOUD_PROVIDER,
)
from sentry_sdk.scope import Scope, should_send_default_pii
from sentry_sdk.traces import SegmentNameSource
from sentry_sdk.tracing import TransactionSource
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.utils import (
    TimeoutThread,
    capture_internal_exceptions,
    event_from_exception,
    has_data_collection_enabled,
    reraise,
)

if TYPE_CHECKING:
    from typing import Any, Callable, TypeVar

    F = TypeVar("F", bound=Callable[..., Any])


def _wrap_handler(handler: "F") -> "F":
    @functools.wraps(handler)
    def sentry_handler(
        aws_event: "Any", aws_context: "Any", *args: "Any", **kwargs: "Any"
    ) -> "Any":
        # Per https://docs.aws.amazon.com/lambda/latest/dg/python-handler.html,
        # `event` here is *likely* a dictionary, but also might be a number of
        # other types (str, int, float, None).
        #
        # In some cases, it is a list (if the user is batch-invoking their
        # function, for example), in which case we'll use the first entry as a
        # representative from which to try pulling request data. (Presumably it
        # will be the same for all events in the list, since they're all hitting
        # the lambda in the same request.)

        client = sentry_sdk.get_client()
        integration = client.get_integration(AwsLambdaIntegration)

        if integration is None:
            return handler(aws_event, aws_context, *args, **kwargs)

        if isinstance(aws_event, list) and len(aws_event) >= 1:
            request_data = aws_event[0]
            batch_size = len(aws_event)
        else:
            request_data = aws_event
            batch_size = 1

        if not isinstance(request_data, dict):
            # If we're not dealing with a dictionary, we won't be able to get
            # headers, path, http method, etc in any case, so it's fine that
            # this is empty
            request_data = {}

        configured_time = aws_context.get_remaining_time_in_millis()
        aws_region = aws_context.invoked_function_arn.split(":")[3]

        with sentry_sdk.isolation_scope() as scope:
            timeout_thread = None
            with capture_internal_exceptions():
                scope.clear_breadcrumbs()
                scope.add_event_processor(
                    _make_request_event_processor(
                        request_data, aws_context, configured_time
                    )
                )
                scope.set_tag("aws_region", aws_region)
                if batch_size > 1:
                    scope.set_tag("batch_request", True)
                    scope.set_tag("batch_size", batch_size)

                # Starting the Timeout thread only if the configured time is greater than Timeout warning
                # buffer and timeout_warning parameter is set True.
                if (
                    integration.timeout_warning
                    and configured_time > TIMEOUT_WARNING_BUFFER
                ):
                    waiting_time = (
                        configured_time - TIMEOUT_WARNING_BUFFER
                    ) / MILLIS_TO_SECONDS

                    timeout_thread = TimeoutThread(
                        waiting_time,
                        configured_time / MILLIS_TO_SECONDS,
                        isolation_scope=scope,
                        current_scope=sentry_sdk.get_current_scope(),
                    )

                    # Starting the thread to raise timeout warning exception
                    timeout_thread.start()

            headers = request_data.get("headers", {})
            # Some AWS Services (ie. EventBridge) set headers as a list
            # or None, so we must ensure it is a dict
            if not isinstance(headers, dict):
                headers = {}

            header_attributes: "dict[str, Any]" = {}
            for header, header_value in _filter_headers(
                headers, use_annotated_value=False
            ).items():
                header_attributes[f"http.request.header.{header.lower()}"] = (
                    header_value
                )

            additional_attributes: "dict[str, Any]" = {}
            if "httpMethod" in request_data:
                additional_attributes["http.request.method"] = request_data[
                    "httpMethod"
                ]

            if "queryStringParameters" in request_data:
                qs = request_data["queryStringParameters"]
                if qs:
                    if has_data_collection_enabled(client.options):
                        filtered_qs = _apply_key_value_collection_filtering(
                            items=qs,
                            behaviour=client.options["data_collection"][
                                "url_query_params"
                            ],
                        )
                        if filtered_qs:
                            additional_attributes["url.query"] = urlencode(filtered_qs)
                    elif should_send_default_pii():
                        additional_attributes["url.query"] = urlencode(qs)

            if not scope._user:
                if has_data_collection_enabled(client.options):
                    if client.options["data_collection"]["user_info"]:
                        user_info = _get_user_from_event(request_data)
                        if user_info:
                            scope.set_user(user_info)
                elif should_send_default_pii():
                    user_info = _get_user_from_event(request_data)
                    if user_info:
                        scope.set_user(user_info)

            sampling_context = {
                "aws_event": aws_event,
                "aws_context": aws_context,
            }

            function_name = aws_context.function_name

            if has_span_streaming_enabled(client.options):
                sentry_sdk.traces.continue_trace(headers)
                Scope.set_custom_sampling_context(sampling_context)
                span_ctx = sentry_sdk.traces.start_span(
                    name=function_name,
                    parent_span=None,
                    attributes={
                        "sentry.op": OP.FUNCTION_AWS,
                        "sentry.origin": ORIGIN,
                        "sentry.segment.name.source": SegmentNameSource.COMPONENT,
                        "cloud.region": aws_region,
                        "cloud.resource_id": aws_context.invoked_function_arn,
                        "cloud.platform": CLOUD_PLATFORM.AWS_LAMBDA,
                        "cloud.provider": CLOUD_PROVIDER.AWS,
                        "faas.name": function_name,
                        "faas.invocation_id": aws_context.aws_request_id,
                        "faas.version": aws_context.function_version,
                        "aws.lambda.invoked_arn": aws_context.invoked_function_arn,
                        "aws.log.group.names": [aws_context.log_group_name],
                        "aws.log.stream.names": [aws_context.log_stream_name],
                        "messaging.batch.message_count": batch_size,
                        **header_attributes,
                        **additional_attributes,
                    },
                )
            else:
                transaction = continue_trace(
                    headers,
                    op=OP.FUNCTION_AWS,
                    name=function_name,
                    source=TransactionSource.COMPONENT,
                    origin=ORIGIN,
                )

                span_ctx = sentry_sdk.start_transaction(
                    transaction, custom_sampling_context=sampling_context
                )

            with span_ctx:
                try:
                    return handler(aws_event, aws_context, *args, **kwargs)
                except Exception:
                    exc_info = sys.exc_info()
                    sentry_event, hint = event_from_exception(
                        exc_info,
                        client_options=client.options,
                        mechanism={"type": "aws_lambda", "handled": False},
                    )
                    sentry_sdk.capture_event(sentry_event, hint=hint)
                    reraise(*exc_info)
                finally:
                    if timeout_thread:
                        timeout_thread.stop()

    return sentry_handler  # type: ignore
