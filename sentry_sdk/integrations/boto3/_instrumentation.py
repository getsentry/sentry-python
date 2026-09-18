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

    request_id = next(
        (
            value
            for value in (
                metadata.get("RequestId"),
                headers.get("x-amzn-requestid"),
                headers.get("x-amzn-request-id"),
                headers.get("x-amz-request-id"),
            )
            if isinstance(value, str) and value
        ),
        None,
    )
    if request_id is not None:
        attributes[SPANDATA.AWS_REQUEST_ID] = request_id

    # S3's `HostId` is the extended request ID returned in `x-amz-id-2`.
    # https://docs.aws.amazon.com/AmazonS3/latest/developerguide/get-request-ids.html
    extended_request_id = next(
        (
            value
            for value in (metadata.get("HostId"), headers.get("x-amz-id-2"))
            if isinstance(value, str) and value
        ),
        None,
    )
    if extended_request_id is not None:
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
            # `StreamingBody` responses outlive `_make_api_call()`. `_activate_client_span()`
            # activates this span only while the call itself runs.
            active=False,
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


def _finish_span(
    span: "Union[Span, StreamedSpan]",
    error: "Optional[BaseException]" = None,
) -> None:
    with capture_internal_exceptions():
        if not isinstance(span, StreamedSpan):
            if error is not None:
                span.set_status(SPANSTATUS.INTERNAL_ERROR)
            span.finish()
            return

        if error is None:
            span.end()
        else:
            span.__exit__(type(error), error, error.__traceback__)


def _instrument_streaming_body(
    span: "Union[Span, StreamedSpan]", parsed: "Dict[str, Any]"
) -> bool:
    if isinstance(span, NoOpStreamedSpan):
        return False

    body = parsed.get("Body")
    if not isinstance(body, StreamingBody):
        return False

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
    raw_stream = body._raw_stream  # type: ignore[attr-defined]
    orig_raw_close = raw_stream.close
    finished = False

    def finish(error: "Optional[BaseException]" = None) -> None:
        nonlocal finished
        if finished:
            return

        finished = True
        if error is not None:
            with capture_internal_exceptions():
                attributes = _get_error_attributes(error)
                _set_span_attributes(streaming_span, attributes)
                if isinstance(span, StreamedSpan):
                    _set_span_attributes(span, attributes)

        _finish_span(streaming_span, error)
        if isinstance(span, StreamedSpan):
            _finish_span(span, error)

    def content_length_reached() -> bool:
        content_length = getattr(body, "_content_length", None)
        amount_read = getattr(body, "_amount_read", None)
        return (
            content_length is not None
            and amount_read is not None
            and amount_read >= int(content_length)
        )

    def sentry_streaming_body_read(*args: "Any", **kwargs: "Any") -> bytes:
        try:
            ret = orig_read(*args, **kwargs)
            with capture_internal_exceptions():
                amount = args[0] if args else kwargs.get("amt")
                if (
                    amount is None
                    or amount < 0
                    or (amount > 0 and not ret)
                    or content_length_reached()
                ):
                    finish()
            return ret
        except BaseException as error:
            finish(error)
            raise

    def sentry_streaming_body_close(*args: "Any", **kwargs: "Any") -> None:
        try:
            orig_close(*args, **kwargs)
            finish()
        except BaseException as error:
            finish(error)
            raise

    def sentry_raw_stream_close(*args: "Any", **kwargs: "Any") -> None:
        try:
            orig_raw_close(*args, **kwargs)
            finish()
        except BaseException as error:
            finish(error)
            raise

    try:
        # StreamingBody.__exit__ closes `_raw_stream` directly, bypassing
        # StreamingBody.close(), so both levels need to be instrumented.
        raw_stream.close = sentry_raw_stream_close
        body.read = sentry_streaming_body_read  # type: ignore
        body.close = sentry_streaming_body_close  # type: ignore
    except Exception:
        finish()
        raise

    return True


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

        span = (
            sentry_sdk.traces.get_current_span()
            if has_span_streaming_enabled(client.options)
            else sentry_sdk.get_current_span()
        )
        if span is None:
            return

        # an ignored streamed span is not activated; avoid enriching its parent.
        if isinstance(span, StreamedSpan) and (
            span.get_attributes().get(SPANDATA.SENTRY_ORIGIN) != Boto3Integration.origin
        ):
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
