import functools
import json
import sys
from contextlib import nullcontext
from os import environ
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.data_collection import _apply_key_value_collection_filtering
from sentry_sdk.integrations._wsgi_common import _filter_headers
from sentry_sdk.integrations.aws_lambda._request import (
    _get_user_from_event,
    _make_request_event_processor,
)
from sentry_sdk.integrations.aws_lambda._trigger import (
    _get_trigger,
    _start_process_span,
)
from sentry_sdk.integrations.aws_lambda.consts import (
    CLOUD_PLATFORM,
    CLOUD_PROVIDER,
    IDENTIFIER,
    LATEST_FUNCTION_VERSION,
    ORIGIN,
)
from sentry_sdk.scope import Scope
from sentry_sdk.traces import SENTRY_TRACE_HEADER_NAME, SegmentNameSource
from sentry_sdk.tracing_utils import extract_sentrytrace_data
from sentry_sdk.utils import (
    TimeoutThread,
    capture_internal_exceptions,
    event_from_exception,
    reraise,
)

if TYPE_CHECKING:
    from typing import Any, Callable, ContextManager, Mapping, TypeVar

    from sentry_sdk._types import Attributes, SpanLink

    F = TypeVar("F", bound=Callable[..., Any])

TIMEOUT_WARNING_BUFFER = 1500
MILLIS_TO_SECONDS = 1000.0
# OTel's default link limit; Lambda delivers up to 10,000 SQS messages at once.
# https://opentelemetry.io/docs/specs/otel/trace/sdk/#span-limits
MAX_PRODUCER_LINKS = 128


def _get_producer_links(aws_event: "Any", trace_id: str) -> "list[SpanLink]":
    """Links to the spans that sent the SQS or SNS messages this invocation got."""
    records: "list[Any]" = []
    if isinstance(aws_event, dict):
        records = aws_event.get("Records") or []

    links: "list[SpanLink]" = []
    for record in records:
        if "Sns" in record:
            # https://docs.aws.amazon.com/lambda/latest/dg/with-sns.html
            sentry_trace = (
                record["Sns"]
                .get("MessageAttributes", {})
                .get(SENTRY_TRACE_HEADER_NAME, {})
                .get("Value")
            )
        else:
            # https://docs.aws.amazon.com/lambda/latest/dg/with-sqs.html
            sentry_trace = (
                record.get("messageAttributes", {})
                .get(SENTRY_TRACE_HEADER_NAME, {})
                .get("stringValue")
            )
            # SNS wraps the message and its attributes in a JSON envelope when raw message delivery is off.
            # https://docs.aws.amazon.com/sns/latest/dg/sns-large-payload-raw-message-delivery.html
            body = record.get("body", "")
            if sentry_trace is None and f'"{SENTRY_TRACE_HEADER_NAME}"' in body:
                try:
                    envelope = json.loads(body)
                except ValueError:
                    envelope = None
                if (
                    isinstance(envelope, dict)
                    and envelope.get("Type") == "Notification"
                ):
                    sentry_trace = (
                        envelope.get("MessageAttributes", {})
                        .get(SENTRY_TRACE_HEADER_NAME, {})
                        .get("Value")
                    )

        data = extract_sentrytrace_data(sentry_trace)
        if data is None or not data["trace_id"] or not data["parent_span_id"]:
            continue
        # the invocation continues this trace, so a link would point into its own trace.
        if data["trace_id"] == trace_id:
            continue

        link: "SpanLink" = {
            "trace_id": str(data["trace_id"]),
            "span_id": str(data["parent_span_id"]),
            "attributes": {SPANDATA.SENTRY_LINK_TYPE: "message_origin"},
        }
        if data["parent_sampled"] is not None:
            link["sampled"] = bool(data["parent_sampled"])
        # a batch send gives many messages the same producer span.
        if link not in links:
            links.append(link)
        if len(links) >= MAX_PRODUCER_LINKS:
            break

    return links


def _wrap_handler(handler: "F") -> "F":
    from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration

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
        else:
            request_data = aws_event

        if not isinstance(request_data, dict):
            # If we're not dealing with a dictionary, we won't be able to get
            # headers, path, http method, etc in any case, so it's fine that
            # this is empty
            request_data = {}

        configured_time = aws_context.get_remaining_time_in_millis()
        with sentry_sdk.isolation_scope() as scope:
            timeout_thread = None
            with capture_internal_exceptions():
                scope.clear_breadcrumbs()
                scope.add_event_processor(
                    _make_request_event_processor(
                        request_data, aws_context, configured_time
                    )
                )

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
            filtered_headers: "Mapping[str, Any]" = {}
            with capture_internal_exceptions():
                filtered_headers = _filter_headers(headers)
            for header, header_value in filtered_headers.items():
                with capture_internal_exceptions():
                    header_attributes[
                        f"{SPANDATA.HTTP_REQUEST_HEADER}.{header.lower()}"
                    ] = [header_value]

            additional_attributes: "dict[str, Any]" = {}
            if "httpMethod" in request_data:
                additional_attributes[SPANDATA.HTTP_REQUEST_METHOD] = request_data[
                    "httpMethod"
                ]

            data_collection = client.options["data_collection"]
            if "queryStringParameters" in request_data:
                qs = request_data["queryStringParameters"]
                if qs:
                    filtered_qs = _apply_key_value_collection_filtering(
                        items=qs,
                        behaviour=data_collection["url_query_params"],
                    )
                    if filtered_qs:
                        additional_attributes[SPANDATA.URL_QUERY] = urlencode(
                            filtered_qs
                        )

            if not scope._user and data_collection["user_info"]:
                user_info = _get_user_from_event(request_data)
                if user_info:
                    scope.set_user(user_info)

            sampling_context = {
                "aws_event": aws_event,
                "aws_context": aws_context,
            }

            function_name = aws_context.function_name

            attributes: "Attributes" = {
                SPANDATA.SENTRY_OP: OP.FUNCTION_AWS,
                SPANDATA.SENTRY_ORIGIN: ORIGIN,
                SPANDATA.SENTRY_KIND: "server",
                SPANDATA.SENTRY_SEGMENT_NAME_SOURCE: SegmentNameSource.COMPONENT,
                SPANDATA.CLOUD_PROVIDER: CLOUD_PROVIDER,
                SPANDATA.CLOUD_PLATFORM: CLOUD_PLATFORM,
                **header_attributes,
                **additional_attributes,
            }

            with capture_internal_exceptions():
                attributes[SPANDATA.FAAS_TRIGGER] = _get_trigger(aws_event)

            with capture_internal_exceptions():
                invoked_function_arn = aws_context.invoked_function_arn
                function_version = aws_context.function_version
                arn_parts = invoked_function_arn.split(":")
                resource_id = ":".join(arn_parts[:7])
                if function_version != LATEST_FUNCTION_VERSION:
                    resource_id = f"{resource_id}:{function_version}"

                attributes.update(
                    {
                        SPANDATA.CLOUD_REGION: environ["AWS_REGION"],
                        SPANDATA.CLOUD_ACCOUNT_ID: arn_parts[4],
                        SPANDATA.CLOUD_RESOURCE_ID: resource_id,
                        SPANDATA.FAAS_NAME: function_name,
                        SPANDATA.FAAS_VERSION: function_version,
                        SPANDATA.FAAS_INVOCATION_ID: aws_context.aws_request_id,
                        SPANDATA.AWS_LAMBDA_INVOKED_ARN: invoked_function_arn,
                        SPANDATA.AWS_LOG_GROUP_NAMES: [aws_context.log_group_name],
                        SPANDATA.AWS_LOG_STREAM_NAMES: [aws_context.log_stream_name],
                    }
                )

            # a synchronous `Invoke` passes the invoking span in `ClientContext`; it wins over
            # event headers, which a forwarded HTTP event keeps from further upstream.
            # https://docs.aws.amazon.com/lambda/latest/dg/python-context.html
            client_context = getattr(aws_context, "client_context", None)
            custom = getattr(client_context, "custom", None)
            if custom and SENTRY_TRACE_HEADER_NAME in custom:
                sentry_sdk.continue_trace(custom)
            else:
                sentry_sdk.continue_trace(headers)

            # SQS and SNS messages link to their producers from the `queue.process` span;
            # they never parent the invocation.
            producer_links: "list[SpanLink]" = []
            with capture_internal_exceptions():
                producer_links = _get_producer_links(
                    aws_event,
                    sentry_sdk.get_current_scope()
                    .get_active_propagation_context()
                    .trace_id,
                )

            Scope.set_custom_sampling_context(sampling_context)
            with sentry_sdk.start_span(
                name=function_name,
                parent_span=None,
                attributes=attributes,
            ):
                try:
                    process_span: "ContextManager[Any]" = nullcontext()
                    with capture_internal_exceptions():
                        process_span = _start_process_span(aws_event, producer_links)
                    with process_span:
                        return handler(aws_event, aws_context, *args, **kwargs)
                except Exception:
                    exc_info = sys.exc_info()
                    sentry_event, hint = event_from_exception(
                        exc_info,
                        mechanism={"type": IDENTIFIER, "handled": False},
                    )
                    sentry_sdk.capture_event(sentry_event, hint=hint)
                    reraise(*exc_info)
                finally:
                    if timeout_thread:
                        timeout_thread.stop()

    return sentry_handler  # type: ignore
