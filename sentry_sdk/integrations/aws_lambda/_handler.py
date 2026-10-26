import functools
import sys
import time
from datetime import datetime, timezone
from os import environ
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.aws_lambda._request import (
    _get_cloudwatch_logs_url,
    _get_http_attributes,
    _get_user_from_event,
    _make_request_event_processor,
)
from sentry_sdk.integrations.aws_lambda._trigger import (
    _get_messaging_batch_size,
    _get_trigger,
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
from sentry_sdk.utils import (
    TimeoutThread,
    capture_internal_exceptions,
    event_from_exception,
    reraise,
)

if TYPE_CHECKING:
    from typing import Any, Callable, TypeVar

    from sentry_sdk._types import Attributes

    F = TypeVar("F", bound=Callable[..., Any])

TIMEOUT_WARNING_BUFFER = 1500
MILLIS_TO_SECONDS = 1000.0
BYTES_PER_MEBIBYTE = 1024 * 1024
# a first invocation this long after the SDK loaded found an environment initialized ahead of demand.
# https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtime-environment.html#runtimes-lifecycle-ib
PROACTIVE_INITIALIZATION_THRESHOLD_SECONDS = 10

_sdk_loaded_at = time.monotonic()
_cold_start = True


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

        global _cold_start
        cold_start = _cold_start
        _cold_start = False

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

            http_attributes: "Attributes" = {}
            with capture_internal_exceptions():
                http_attributes = _get_http_attributes(request_data)

            if not scope._user and client.options["data_collection"]["user_info"]:
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
                **http_attributes,
            }

            with capture_internal_exceptions():
                attributes[SPANDATA.FAAS_TRIGGER] = _get_trigger(aws_event)
                # HTTP and direct invocations aren't batches, even when the payload is a list.
                messaging_batch_size = _get_messaging_batch_size(aws_event)
                if messaging_batch_size is not None:
                    attributes[SPANDATA.MESSAGING_BATCH_MESSAGE_COUNT] = (
                        messaging_batch_size
                    )

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
                        SPANDATA.FAAS_INSTANCE: aws_context.log_stream_name,
                        SPANDATA.FAAS_COLDSTART: cold_start,
                    }
                )

            # https://docs.aws.amazon.com/lambda/latest/dg/configuration-envvars.html#configuration-envvars-runtime
            with capture_internal_exceptions():
                initialization_type = environ["AWS_LAMBDA_INITIALIZATION_TYPE"]
                attributes.update(
                    {
                        SPANDATA.FAAS_MAX_MEMORY: int(
                            environ["AWS_LAMBDA_FUNCTION_MEMORY_SIZE"]
                        )
                        * BYTES_PER_MEBIBYTE,
                        # the Sentry layer keeps the function's handler in `SENTRY_INITIAL_HANDLER`.
                        SPANDATA.FAAS_ENTRY_POINT: environ.get("SENTRY_INITIAL_HANDLER")
                        or environ["_HANDLER"],
                        SPANDATA.AWS_LAMBDA_INITIALIZATION_TYPE: initialization_type,
                    }
                )

                # provisioned concurrency and SnapStart say so in the initialization type.
                if (
                    cold_start
                    and initialization_type == "on-demand"
                    and time.monotonic() - _sdk_loaded_at
                    > PROACTIVE_INITIALIZATION_THRESHOLD_SECONDS
                ):
                    attributes[SPANDATA.AWS_LAMBDA_PROACTIVE_INITIALIZATION] = True

            # a synchronous `Invoke` passes the invoking span in `ClientContext`; it wins over
            # event headers, which a forwarded HTTP event keeps from further upstream.
            # https://docs.aws.amazon.com/lambda/latest/dg/python-context.html
            client_context = aws_context.client_context
            if (
                client_context is not None
                and client_context.custom
                and SENTRY_TRACE_HEADER_NAME in client_context.custom
            ):
                sentry_sdk.continue_trace(client_context.custom)
            else:
                sentry_sdk.continue_trace(headers)
            Scope.set_custom_sampling_context(sampling_context)
            with sentry_sdk.start_span(
                name=function_name,
                parent_span=None,
                attributes=attributes,
            ) as span:
                start_time = datetime.now(timezone.utc)
                start = time.monotonic()
                try:
                    response = handler(aws_event, aws_context, *args, **kwargs)
                    with capture_internal_exceptions():
                        # the response an HTTP integration turns into the HTTP response.
                        if (
                            SPANDATA.HTTP_REQUEST_METHOD in http_attributes
                            and isinstance(response, dict)
                            and "statusCode" in response
                        ):
                            status_code = int(response["statusCode"])
                            span.status = "error" if status_code >= 400 else "ok"
                            span.set_attribute(SPANDATA.HTTP_STATUS_CODE, status_code)
                    return response
                except Exception as exception:
                    exception_type = type(exception)
                    span.set_attribute(
                        SPANDATA.ERROR_TYPE,
                        exception_type.__qualname__
                        if exception_type.__module__ == "builtins"
                        else f"{exception_type.__module__}.{exception_type.__qualname__}",
                    )

                    exc_info = sys.exc_info()
                    sentry_event, hint = event_from_exception(
                        exc_info,
                        mechanism={"type": IDENTIFIER, "handled": False},
                    )
                    sentry_sdk.capture_event(sentry_event, hint=hint)
                    reraise(*exc_info)
                finally:
                    with capture_internal_exceptions():
                        duration = (time.monotonic() - start) * 1000
                        span.set_attributes(
                            {
                                SPANDATA.AWS_LAMBDA_EXECUTION_DURATION_IN_MILLIS: duration,
                                SPANDATA.FAAS_DURATION_IN_MS: int(duration),
                                SPANDATA.AWS_LAMBDA_REMAINING_TIME_IN_MILLIS: aws_context.get_remaining_time_in_millis(),
                                SPANDATA.AWS_CLOUDWATCH_LOGS_URL: _get_cloudwatch_logs_url(
                                    aws_context, start_time
                                ),
                            }
                        )

                    if timeout_thread:
                        timeout_thread.stop()

    return sentry_handler  # type: ignore
