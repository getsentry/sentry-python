from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA, SPANSTATUS
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.boto3 import Boto3Integration
from sentry_sdk.traces import NoOpStreamedSpan, StreamedSpan
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

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext

try:
    from botocore.awsrequest import AWSRequest
    from botocore.exceptions import ClientError
    from botocore.response import StreamingBody
except ImportError:
    raise DidNotEnable("botocore not installed")


_AWS_RPC_SYSTEM_NAME = "aws-api"
_STDLIB_HTTP_SPAN_ORIGIN = "auto.http.stdlib.httplib"


def _set_span_attributes(
    span: "Union[Span, StreamedSpan]", attributes: "Attributes"
) -> None:
    """Will be removed in the major."""
    if isinstance(span, StreamedSpan):
        span.set_attributes(attributes)
        return

    for key, value in attributes.items():
        span.set_data(key, value)


def _get_server_attributes(endpoint_url: "Optional[str]") -> "Attributes":
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

        # `server.port` is only defined together with `server.address`.
        # Infer the effective port when the configured HTTP(S) endpoint omits it.
        # https://opentelemetry.io/docs/specs/semconv/rpc/rpc-spans/
        return {
            SPANDATA.SERVER_ADDRESS: parsed_url.hostname,
            SPANDATA.SERVER_PORT: parsed_url.port or default_ports[parsed_url.scheme],
        }

    except (TypeError, UnicodeError, ValueError):
        # Invalid client metadata must not prevent the AWS call from running.
        return {}


def _get_client_attributes(
    ctx: "AwsCallContext",
) -> "Attributes":
    attributes: "Attributes" = {}

    # `rpc.service` is deprecated in OTel, but js still uses it.
    if ctx.service_id:
        attributes[SPANDATA.RPC_SERVICE] = ctx.service_id

    if ctx.region_name:
        attributes[SPANDATA.CLOUD_REGION] = ctx.region_name

    attributes.update(_get_server_attributes(ctx.endpoint_url))
    return attributes


def _get_response_attributes(response: "Any") -> "Attributes":
    if not isinstance(response, dict):
        return {}

    metadata = response.get("ResponseMetadata")
    if not isinstance(metadata, dict):
        return {}
    attributes: "Attributes" = {}

    # botocore injects HTTP status into `ResponseMetadata` after parsing.
    # https://github.com/boto/botocore/blob/develop/botocore/parsers.py#L273-L284
    status_code = metadata.get("HTTPStatusCode")
    if isinstance(status_code, int) and 100 <= status_code <= 599:
        attributes[SPANDATA.HTTP_STATUS_CODE] = status_code

    retry_attempts = metadata.get("RetryAttempts")
    # botocore represents retries as `attempts - 1`; OTel suggests "if and only if", so skip zero.
    # https://github.com/boto/botocore/blob/develop/botocore/endpoint.py#L221-L229
    # https://opentelemetry.io/docs/specs/semconv/http/http-spans/#http-client-span
    if (
        isinstance(retry_attempts, int)
        # avoid emitting `resend_count=True`.
        and not isinstance(retry_attempts, bool)
        and retry_attempts > 0
    ):
        attributes[SPANDATA.HTTP_REQUEST_RESEND_COUNT] = retry_attempts

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
        # `ClientError` wraps AWS service errors; `Error.Code` identifies the
        # actual service error, e.g. `AccessDeniedException`.
        # https://docs.aws.amazon.com/boto3/latest/guide/error-handling.html
        error = exception.response.get("Error")
        if isinstance(error, dict):
            error_code = error.get("Code")
            if isinstance(error_code, str) and error_code:
                return error_code

    # failures before a service response have no AWS error code.
    # https://opentelemetry.io/docs/specs/semconv/rpc/rpc-spans/
    exception_type = type(exception)
    exception_name = exception_type.__qualname__
    exception_module = exception_type.__module__
    if exception_module not in ("builtins", "__builtins__"):
        return "%s.%s" % (exception_module, exception_name)
    return exception_name


def _get_error_attributes(exception: "BaseException") -> "Attributes":
    attributes: "Attributes" = {}
    if isinstance(exception, ClientError):
        attributes.update(_get_response_attributes(exception.response))

    attributes[SPANDATA.ERROR_TYPE] = _get_error_type(exception)
    return attributes


def _start_client_span(
    ctx: "AwsCallContext",
) -> "Optional[Union[Span, StreamedSpan]]":
    client = sentry_sdk.get_client()
    if client.get_integration(Boto3Integration) is None:
        return None

    # use unknown if `service_id_hyphenized` so span name can still be created.
    # e.g. "aws.unkown.GetObject"
    service_name = ctx.service_id_hyphenized or "unknown"
    span_name = "aws.%s.%s" % (service_name, ctx.operation_name)
    attributes: "Attributes" = {
        SPANDATA.RPC_METHOD: ctx.operation_name,
        SPANDATA.RPC_SYSTEM_NAME: _AWS_RPC_SYSTEM_NAME,
    }
    with capture_internal_exceptions():
        attributes.update(_get_client_attributes(ctx))
    span_op = OP.HTTP_CLIENT
    span_origin = Boto3Integration.origin

    if has_span_streaming_enabled(client.options):
        if sentry_sdk.traces.get_current_span() is None:
            return None

        # `start_span()` evaluates `ignore_spans` against the initial attributes.
        # https://opentelemetry.io/docs/specs/semconv/rpc/rpc-spans/#rpc-client-span
        attributes.update(
            {
                SPANDATA.SENTRY_OP: span_op,
                SPANDATA.SENTRY_ORIGIN: span_origin,
            }
        )
        return sentry_sdk.traces.start_span(
            name=span_name,
            attributes=attributes,
        )

    span = sentry_sdk.start_span(
        name=span_name,
        op=span_op,
        origin=span_origin,
    )
    with capture_internal_exceptions():
        _set_span_attributes(span, attributes)
    with capture_internal_exceptions():
        if ctx.service_id_hyphenized:
            span.set_tag("aws.service_id", ctx.service_id_hyphenized)
        span.set_tag("aws.operation_name", ctx.operation_name)
    return span


def _finish_active_http_child_span(
    span: "Union[Span, StreamedSpan]",
) -> None:
    if not isinstance(span, StreamedSpan):
        return

    http_span = sentry_sdk.traces.get_current_span()
    if (
        http_span is None
        or http_span is span
        or http_span.get_attributes().get(SPANDATA.SENTRY_ORIGIN)
        != _STDLIB_HTTP_SPAN_ORIGIN
        or http_span._parent_span_id != span.span_id
    ):
        return

    # Stdlib normally keeps its HTTP span open until the response body is read.
    # Boto3 has a separate `http.client.stream` span for that work, so finish the
    # HTTP span after the headers and preserve LIFO scope restoration. OTel permits
    # HTTP client spans to end after response headers are read.
    # https://opentelemetry.io/docs/specs/semconv/http/http-spans/#http-client-span-duration
    http_span.end()


def _instrument_streaming_body(
    span: "Union[Span, StreamedSpan]",
    parsed: "Dict[str, Any]",
) -> None:
    if isinstance(span, NoOpStreamedSpan):
        return

    body = parsed.get("Body")
    if not isinstance(body, StreamingBody):
        return

    streaming_span: "Union[Span, StreamedSpan]"
    if isinstance(span, StreamedSpan):
        streaming_span = sentry_sdk.traces.start_span(
            name=span.name,
            parent_span=span,
            active=False,
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
            # an ignored `NoOpStreamedSpan` is not activated, so
            # `get_current_span()` may return the parent; do not enrich it.
            if (
                span is None
                or span.get_attributes().get(SPANDATA.SENTRY_ORIGIN)
                != Boto3Integration.origin
            ):
                return
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
