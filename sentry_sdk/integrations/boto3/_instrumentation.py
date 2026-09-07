from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError
from botocore.response import StreamingBody

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA, SPANSTATUS
from sentry_sdk.integrations.boto3 import Boto3Integration
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing import BAGGAGE_HEADER_NAME, Span
from sentry_sdk.tracing_utils import (
    add_http_breadcrumb,
    add_sentry_baggage_to_headers,
    get_url_attributes,
    has_span_streaming_enabled,
    should_propagate_trace,
)
from sentry_sdk.utils import (
    capture_internal_exceptions,
    parse_url,
)

if TYPE_CHECKING:
    from typing import Any, Dict, Optional, Union

    from sentry_sdk.integrations.boto3._client import _ClientCallContext

_AWS_RPC_SYSTEM_NAME = "aws-api"


def _set_span_attributes(
    span: "Union[Span, StreamedSpan]", attributes: "Dict[str, Any]"
) -> None:
    # streamed and legacy spans expose different attribute APIs.
    if isinstance(span, StreamedSpan):
        span.set_attributes(attributes)
        return

    for key, value in attributes.items():
        span.set_data(key, value)


def _get_server_attributes(endpoint_url: "Optional[str]") -> "Dict[str, Any]":
    if not endpoint_url:
        return {}

    default_ports = {
        "http": 80,
        "https": 443,
    }

    try:
        parsed_url = urlsplit(endpoint_url)
        if parsed_url.scheme not in default_ports or not parsed_url.hostname:
            return {}

        # `server.port` is only defined together with `server.address`. Infer the
        # effective port when the configured HTTP(S) endpoint omits it.
        # https://opentelemetry.io/docs/specs/semconv/rpc/rpc-spans/
        return {
            SPANDATA.SERVER_ADDRESS: parsed_url.hostname,
            SPANDATA.SERVER_PORT: parsed_url.port or default_ports[parsed_url.scheme],
        }

    except (TypeError, UnicodeError, ValueError):
        # Invalid client metadata must not prevent the AWS call from running.
        return {}


def _get_client_span_attributes(
    call_context: "_ClientCallContext",
) -> "Dict[str, Any]":
    # AWS keeps service and operation separate, so `rpc.method` is only the
    # operation. https://opentelemetry.io/docs/specs/semconv/cloud-providers/aws-sdk/#aws-sdk-spans
    attributes = {
        SPANDATA.RPC_METHOD: call_context.operation_name,
        SPANDATA.RPC_SYSTEM_NAME: _AWS_RPC_SYSTEM_NAME,
    }

    if call_context.region_name:
        attributes[SPANDATA.CLOUD_REGION] = call_context.region_name

    attributes.update(_get_server_attributes(call_context.endpoint_url))
    return attributes


def _get_response_attributes(response: "Any") -> "Dict[str, Any]":
    if not isinstance(response, dict):
        return {}

    metadata = response.get("ResponseMetadata")
    if not isinstance(metadata, dict):
        return {}

    attributes: "Dict[str, Any]" = {}

    status_code = metadata.get("HTTPStatusCode")
    # botocore injects HTTP status into `ResponseMetadata` after parsing.
    # https://github.com/boto/botocore/blob/develop/botocore/parsers.py#L273-L284
    if (
        isinstance(status_code, int)
        and not isinstance(status_code, bool)
        and 100 <= status_code <= 599
    ):
        attributes[SPANDATA.HTTP_STATUS_CODE] = status_code

    retry_attempts = metadata.get("RetryAttempts")
    # botocore represents retries as `attempts - 1`; omit zero.
    # https://github.com/boto/botocore/blob/develop/botocore/endpoint.py#L221-L229
    # https://opentelemetry.io/docs/specs/semconv/http/http-spans/#http-client-span
    if (
        isinstance(retry_attempts, int)
        and not isinstance(retry_attempts, bool)
        and retry_attempts > 0
    ):
        attributes["http.request.resend_count"] = retry_attempts

    headers = metadata.get("HTTPHeaders")
    if not isinstance(headers, dict):
        headers = {}

    request_id = metadata.get("RequestId")
    if not isinstance(request_id, str) or not request_id:
        request_id = next(
            (
                value
                for value in (
                    headers.get("x-amzn-requestid"),
                    headers.get("x-amzn-request-id"),
                    headers.get("x-amz-request-id"),
                )
                if isinstance(value, str) and value
            ),
            None,
        )
    if isinstance(request_id, str) and request_id:
        attributes[SPANDATA.AWS_REQUEST_ID] = request_id

    # S3's `HostId` is the extended request ID returned in `x-amz-id-2`.
    # https://docs.aws.amazon.com/AmazonS3/latest/developerguide/get-request-ids.html
    extended_request_id = metadata.get("HostId")
    if not isinstance(extended_request_id, str) or not extended_request_id:
        extended_request_id = headers.get("x-amz-id-2")
    if isinstance(extended_request_id, str) and extended_request_id:
        attributes[SPANDATA.AWS_EXTENDED_REQUEST_ID] = extended_request_id

    return attributes


def _get_error_type(exception: "BaseException") -> str:
    if isinstance(exception, ClientError):
        # botocore wraps all AWS service errors in `ClientError`; `Error.Code`
        # identifies actual service-specific error, e.g. `AccessDenied`.
        # https://docs.aws.amazon.com/boto3/latest/guide/error-handling.html
        error = exception.response.get("Error")
        if isinstance(error, dict):
            error_code = error.get("Code")
            if isinstance(error_code, str) and error_code:
                return error_code

    # failures before a service response, have no error code. Use exception type
    # instead. https://opentelemetry.io/docs/specs/semconv/rpc/rpc-spans/
    exception_type = type(exception)
    exception_name = exception_type.__qualname__
    exception_module = exception_type.__module__
    if exception_module not in ("builtins", "__builtins__"):
        return "%s.%s" % (exception_module, exception_name)
    return exception_name


def _get_error_attributes(exception: "BaseException") -> "Dict[str, Any]":
    attributes = {}
    if isinstance(exception, ClientError):
        attributes.update(_get_response_attributes(exception.response))

    attributes[SPANDATA.ERROR_TYPE] = _get_error_type(exception)
    return attributes


def _start_client_span(
    call_context: "_ClientCallContext",
) -> "Optional[Union[Span, StreamedSpan]]":
    client = sentry_sdk.get_client()
    if client.get_integration(Boto3Integration) is None:
        return None

    # AWS client spans use `Service.Operation`, e.g. `DynamoDB.GetItem`.
    # https://opentelemetry.io/docs/specs/semconv/cloud-providers/aws-sdk/#aws-sdk-spans
    span_name = "%s.%s" % (call_context.service_id, call_context.operation_name)
    attributes = _get_client_span_attributes(call_context)

    if has_span_streaming_enabled(client.options):
        if sentry_sdk.traces.get_current_span() is None:
            return None

        # `start_span()` evaluates `ignore_spans` against the initial attributes.
        # https://opentelemetry.io/docs/specs/semconv/rpc/rpc-spans/#rpc-client-span
        attributes.update(
            {
                SPANDATA.SENTRY_OP: OP.HTTP_CLIENT,
                SPANDATA.SENTRY_ORIGIN: Boto3Integration.origin,
            }
        )
        return sentry_sdk.traces.start_span(
            name=span_name,
            attributes=attributes,
        )

    span = sentry_sdk.start_span(
        name=span_name,
        op=OP.HTTP_CLIENT,
        origin=Boto3Integration.origin,
    )
    _set_span_attributes(span, attributes)
    span.set_tag("aws.service_id", call_context.service_id.hyphenize())
    span.set_tag("aws.operation_name", call_context.operation_name)
    return span


def _finish_client_span(
    span: "Union[Span, StreamedSpan]",
    parsed: "Dict[str, Any]",
) -> None:
    # response metadata is only available after the call. Keep enrichment
    # isolated so failure cannot prevent `__exit__()` below.
    with capture_internal_exceptions():
        _set_span_attributes(span, _get_response_attributes(parsed))
    span.__exit__(None, None, None)

    body = parsed.get("Body")
    if not isinstance(body, StreamingBody):
        return

    streaming_span: "Union[Span, StreamedSpan]"
    if isinstance(span, StreamedSpan):
        streaming_span = sentry_sdk.traces.start_span(
            name=span.name,
            parent_span=span,
            attributes={
                SPANDATA.SENTRY_OP: OP.HTTP_CLIENT_STREAM,
                SPANDATA.SENTRY_ORIGIN: Boto3Integration.origin,
            },
        )
    else:
        streaming_span = span.start_child(
            op=OP.HTTP_CLIENT_STREAM,
            name=span.description,
            origin=Boto3Integration.origin,
        )

    orig_read = body.read
    orig_close = body.close

    def sentry_streaming_body_read(*args: "Any", **kwargs: "Any") -> bytes:
        try:
            ret = orig_read(*args, **kwargs)
            if ret:
                return ret

            if isinstance(streaming_span, StreamedSpan):
                streaming_span.end()
            else:
                streaming_span.finish()
            return ret
        except Exception as exc:
            # enrichment must not replace exception raised by `orig_read()`.
            # finish span with error, then re-raise.
            with capture_internal_exceptions():
                _set_span_attributes(streaming_span, _get_error_attributes(exc))

            with capture_internal_exceptions():
                if isinstance(streaming_span, StreamedSpan):
                    streaming_span.__exit__(type(exc), exc, exc.__traceback__)
                else:
                    streaming_span.set_status(SPANSTATUS.INTERNAL_ERROR)
                    streaming_span.finish()
            raise

    body.read = sentry_streaming_body_read  # type: ignore

    def sentry_streaming_body_close(*args: "Any", **kwargs: "Any") -> None:
        if isinstance(streaming_span, StreamedSpan):
            streaming_span.end()
        else:
            streaming_span.finish()
        orig_close(*args, **kwargs)

    body.close = sentry_streaming_body_close  # type: ignore


def _finish_client_span_with_error(
    span: "Union[Span, StreamedSpan]",
    exception: "BaseException",
) -> None:
    with capture_internal_exceptions():
        _set_span_attributes(span, _get_error_attributes(exception))
    span.__exit__(type(exception), exception, exception.__traceback__)


def _set_request_attributes(
    span: "Union[Span, StreamedSpan]",
    request: "AWSRequest",
) -> None:
    client = sentry_sdk.get_client()

    parsed_url = None
    if request.url is not None:
        with capture_internal_exceptions():
            parsed_url = parse_url(request.url, sanitize=False)

    if isinstance(span, StreamedSpan):
        span.set_attributes(get_url_attributes(client, parsed_url))

        if request.method is not None:
            span.set_attribute(SPANDATA.HTTP_REQUEST_METHOD, request.method)

        return

    if parsed_url is not None:
        span.set_data("aws.request.url", parsed_url.url)
        span.set_data(SPANDATA.HTTP_QUERY, parsed_url.query)
        span.set_data(SPANDATA.HTTP_FRAGMENT, parsed_url.fragment)

    if request.method is not None:
        span.set_data(SPANDATA.HTTP_METHOD, request.method)


def _add_request_breadcrumb(request: "AWSRequest") -> None:
    client = sentry_sdk.get_client()

    parsed_url = None
    if request.url is not None:
        with capture_internal_exceptions():
            parsed_url = parse_url(request.url, sanitize=False)

    breadcrumb: "dict[str, Any]" = {}

    if has_span_streaming_enabled(client.options):
        breadcrumb.update(get_url_attributes(client, parsed_url))
        if request.method is not None:
            breadcrumb[SPANDATA.HTTP_REQUEST_METHOD] = request.method
    else:
        if parsed_url is not None:
            breadcrumb.update(
                {
                    "aws.request.url": parsed_url.url,
                    SPANDATA.HTTP_QUERY: parsed_url.query,
                    SPANDATA.HTTP_FRAGMENT: parsed_url.fragment,
                }
            )

        if request.method is not None:
            breadcrumb[SPANDATA.HTTP_METHOD] = request.method

    add_http_breadcrumb(None, breadcrumb)


def _sentry_request_created(
    request: "AWSRequest", operation_name: str, **kwargs: "Any"
) -> None:
    """
    Enrich a single `AWSRequest` attempt. Botocore creates a
    fresh `AWSRequest` on every retry.
    https://github.com/boto/botocore/blob/develop/botocore/endpoint.py#L178-L202
    """
    client = sentry_sdk.get_client()
    if client.get_integration(Boto3Integration) is None:
        return

    with capture_internal_exceptions():
        _add_request_breadcrumb(request)

        if has_span_streaming_enabled(client.options):
            span = sentry_sdk.traces.get_current_span()
        else:
            span = sentry_sdk.get_current_span()
        if span is None:
            return

        _set_request_attributes(span, request)
        # each attempt has a fresh `request.context`; carry the active client span.
        request.context["_sentrysdk_span"] = span


def _sentry_before_sign(
    request: "AWSRequest", signature_version: "Any", **kwargs: "Any"
) -> None:
    client = sentry_sdk.get_client()
    if client.get_integration(Boto3Integration) is None:
        return

    with capture_internal_exceptions():
        # presigned requests are executed later by another caller. Adding propagation
        # headers here would make those headers part of the signature, requiring the caller to reproduce the same values.
        if isinstance(signature_version, str) and signature_version.endswith(
            ("-query", "-presign-post")
        ):
            return

        if request.url is None or not should_propagate_trace(client, request.url):
            return

        def _replace_header(request: "AWSRequest", key: str, value: str) -> None:
            """
            Botocore's `HTTPHeaders` inherits from `email.message.Message`, where:
                headers["foo"] = "old"
                headers["foo"] = "new"
            produces two fields: {"foo": "old", "foo": "new"}. So delete existing
            fields before assigning replacement.
            """
            if key in request.headers:
                del request.headers[key]
            request.headers[key] = value

        # use span associated with this botocore request
        span = request.context.get("_sentrysdk_span")

        headers = sentry_sdk.get_current_scope().iter_trace_propagation_headers(
            span=span
        )
        for header_name, header_value in headers:
            if header_name != BAGGAGE_HEADER_NAME:
                # normal headers (e.g. `sentry-trace`) are non-shared, so replace stale values
                _replace_header(request, header_name, header_value)
                continue

            # merge existing `baggage` values under single header
            existing_values = request.headers.get_all(BAGGAGE_HEADER_NAME, [])
            combined_baggage = {
                BAGGAGE_HEADER_NAME: ",".join(str(value) for value in existing_values)
            }
            add_sentry_baggage_to_headers(combined_baggage, header_value)
            _replace_header(
                request, BAGGAGE_HEADER_NAME, combined_baggage[BAGGAGE_HEADER_NAME]
            )
