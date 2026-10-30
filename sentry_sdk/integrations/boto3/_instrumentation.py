from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.boto3.consts import (
    AIOBOTOCORE_IDENTIFIER,
    AWS_RPC_SYSTEM_NAME,
    CLOUD_PROVIDER,
    DEFAULT_PORTS,
    IDENTIFIER,
    ORIGIN,
)
from sentry_sdk.traces import BAGGAGE_HEADER_NAME, NoOpSpan, Span
from sentry_sdk.tracing_utils import (
    add_http_breadcrumb,
    add_sentry_baggage_to_headers,
    get_url_attributes,
    should_propagate_trace,
)
from sentry_sdk.utils import (
    capture_internal_exceptions,
    parse_url,
)

if TYPE_CHECKING:
    from typing import Any, Dict, Mapping, Optional

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext
    from sentry_sdk.integrations.boto3._services.base import _ServiceExtension

try:
    from botocore.awsrequest import AWSRequest
    from botocore.exceptions import ClientError
    from botocore.response import StreamingBody
except ImportError:
    raise DidNotEnable("botocore not installed")


def _is_enabled() -> bool:
    client = sentry_sdk.get_client()
    return (
        client.get_integration(IDENTIFIER) is not None
        or client.get_integration(AIOBOTOCORE_IDENTIFIER) is not None
    )


def _finish_span(span: "Span", error: "Optional[BaseException]" = None) -> None:
    with capture_internal_exceptions():
        if error is None:
            span.end()
        else:
            span.__exit__(type(error), error, error.__traceback__)


def _get_server_attributes(endpoint_url: "Optional[str]") -> "Attributes":
    if not endpoint_url:
        return {}

    try:
        parsed_url = urlsplit(endpoint_url)
        if parsed_url.scheme not in DEFAULT_PORTS or not parsed_url.hostname:
            return {}

        # `server.port` is only defined together with `server.address`.
        # Infer the effective port when the configured HTTP(S) endpoint omits it.
        # https://opentelemetry.io/docs/specs/semconv/rpc/rpc-spans/
        return {
            SPANDATA.SERVER_ADDRESS: parsed_url.hostname,
            SPANDATA.SERVER_PORT: parsed_url.port or DEFAULT_PORTS[parsed_url.scheme],
        }

    except (TypeError, UnicodeError, ValueError):
        # invalid client metadata must not prevent the AWS call from running.
        return {}


def _get_client_attributes(
    ctx: "AwsCallContext",
) -> "Attributes":
    attributes: "Attributes" = {}

    # `rpc.service` is deprecated in OTel, but js still uses it.
    if ctx.service_id is not None:
        attributes[SPANDATA.RPC_SERVICE] = ctx.service_id

    if ctx.region_name:
        attributes[SPANDATA.CLOUD_REGION] = ctx.region_name

    attributes.update(_get_server_attributes(ctx.endpoint_url))
    return attributes


def _get_response_attributes(response: "Mapping[str, Any]") -> "Attributes":
    metadata = response.get("ResponseMetadata", {})
    attributes: "Attributes" = {}

    # botocore injects HTTP status into `ResponseMetadata` after parsing.
    # https://github.com/boto/botocore/blob/358f8eec8c76201bb1a7a35644abcbc9036de7ed/botocore/parsers.py#L273-L284
    status_code = metadata.get("HTTPStatusCode")
    if status_code is not None:
        attributes[SPANDATA.HTTP_STATUS_CODE] = status_code

    retry_attempts = metadata.get("RetryAttempts", 0)
    if retry_attempts > 0:
        attributes[SPANDATA.HTTP_REQUEST_RESEND_COUNT] = retry_attempts

    headers = metadata.get("HTTPHeaders", {})

    request_id = (
        metadata.get("RequestId")
        or headers.get("x-amzn-requestid")
        or headers.get("x-amzn-request-id")
        or headers.get("x-amz-request-id")
    )
    if request_id:
        attributes[SPANDATA.AWS_REQUEST_ID] = request_id

    # S3's `HostId` is the extended request ID returned in `x-amz-id-2`.
    # https://docs.aws.amazon.com/AmazonS3/latest/developerguide/get-request-ids.html
    extended_request_id = metadata.get("HostId") or headers.get("x-amz-id-2")
    if extended_request_id:
        attributes[SPANDATA.AWS_EXTENDED_REQUEST_ID] = extended_request_id

    return attributes


def _get_error_type(exception: "BaseException") -> str:
    if isinstance(exception, ClientError):
        # `ClientError` wraps AWS service errors; `Error.Code` identifies the
        # actual service error, e.g. `AccessDeniedException`.
        # https://docs.aws.amazon.com/boto3/latest/guide/error-handling.html
        error_code: "Optional[str]" = exception.response.get("Error", {}).get("Code")
        if error_code:
            return error_code

    # failures before a service response have no AWS error code.
    # https://opentelemetry.io/docs/specs/semconv/rpc/rpc-spans/
    exception_type = type(exception)
    exception_name = exception_type.__qualname__
    exception_module = exception_type.__module__
    if exception_module not in ("builtins", "__builtins__"):
        return f"{exception_module}.{exception_name}"
    return exception_name


def _get_error_attributes(exception: "BaseException") -> "Attributes":
    attributes: "Attributes" = {}
    if isinstance(exception, ClientError):
        attributes.update(_get_response_attributes(exception.response))

    attributes[SPANDATA.ERROR_TYPE] = _get_error_type(exception)
    return attributes


def _start_client_span(
    ctx: "AwsCallContext",
    service_ext: "Optional[_ServiceExtension]",
    origin: str,
) -> "Optional[Span]":
    if sentry_sdk.get_current_span() is None:
        return None

    # https://opentelemetry.io/docs/specs/semconv/cloud-providers/aws-sdk/#aws-sdk-spans
    service_name = ctx.service_id or "unknown"
    span_name = f"{service_name}.{ctx.operation_name}"
    attributes: "Attributes" = {
        SPANDATA.CLOUD_PROVIDER: CLOUD_PROVIDER,
        SPANDATA.RPC_METHOD: ctx.operation_name,
        SPANDATA.RPC_SYSTEM_NAME: AWS_RPC_SYSTEM_NAME,
        # all client call spans are by default "client" spans.
        # https://opentelemetry.io/docs/specs/semconv/cloud-providers/aws-sdk/#aws-sdk-spans
        SPANDATA.SENTRY_KIND: "client",
    }
    with capture_internal_exceptions():
        attributes.update(_get_client_attributes(ctx))

    # `sentry.op` is set to a generic default, which a service extension can override
    # with `get_span_op()`.
    span_op = OP.RPC

    if service_ext is not None:
        with capture_internal_exceptions():
            service_op = service_ext.get_span_op(ctx)
            if service_op is not None:
                span_op = service_op

        with capture_internal_exceptions():
            attributes.update(service_ext.get_request_attributes(ctx))

    # `start_span()` evaluates `ignore_spans` against the initial attributes.
    # https://opentelemetry.io/docs/specs/semconv/rpc/rpc-spans/#rpc-client-span
    attributes.update(
        {
            SPANDATA.SENTRY_OP: span_op,
            SPANDATA.SENTRY_ORIGIN: origin,
        }
    )
    return sentry_sdk.start_span(
        name=span_name,
        attributes=attributes,
        # `StreamingBody` responses outlive `_make_api_call()`. `_activate_client_span()`
        # activates this span only while the call itself runs.
        active=False,
    )


def _find_streaming_body(parsed: "Dict[str, Any]") -> "Optional[StreamingBody]":
    # botocore uses service-specific response key for streaming payload; e.g. `Payload`
    # for Lambda Invoke, `Body` for S3 GetObject. Find it by type so every streaming
    # response is handled.
    return next(
        (value for value in parsed.values() if isinstance(value, StreamingBody)),
        None,
    )


class _StreamingBodySpans:
    """
    The `http.client.stream` span of a streaming response, ended together with its
    client span when the body is read to its end, closed or fails.
    """

    __slots__ = ("_span", "_streaming_span", "_body", "_finished")

    def __init__(self, span: "Span", body: "StreamingBody", origin: str) -> None:
        self._span = span
        self._body = body
        self._finished = False
        self._streaming_span = sentry_sdk.start_span(
            name=span.name,
            # keep stream span under the boto span after `_make_api_call()` returns.
            parent_span=span,
            # the body may outlive the api call, so keep it inactive. Otherwise it
            # 1. could restore the already-finished client span when it ends; 2. make
            # unrelated new spans attach to the stream span since it's the current span.
            active=False,
            attributes={
                SPANDATA.SENTRY_OP: OP.HTTP_CLIENT_STREAM,
                SPANDATA.SENTRY_ORIGIN: origin,
            },
        )

    def finish(self, error: "Optional[BaseException]" = None) -> None:
        if self._finished:
            return

        self._finished = True
        # finish the stream span before the client span, and only once across
        # read and close.
        if error is not None:
            with capture_internal_exceptions():
                attributes = _get_error_attributes(error)
                self._streaming_span.set_attributes(attributes)
                self._span.set_attributes(attributes)

        _finish_span(self._streaming_span, error)
        _finish_span(self._span, error)

    def finish_if_read(
        self, amount_of_bytes_requested: "Optional[int]", chunk: bytes
    ) -> None:
        content_length = self._body._content_length  # type: ignore[attr-defined]
        # detect read-to-end, eof, or the known content length being consumed.
        if (
            amount_of_bytes_requested is None
            or amount_of_bytes_requested < 0
            or (amount_of_bytes_requested > 0 and not chunk)
            or (
                content_length is not None
                and self._body._amount_read >= int(content_length)  # type: ignore[attr-defined]
            )
        ):
            self.finish()


def _instrument_streaming_body(span: "Span", parsed: "Dict[str, Any]") -> bool:
    if isinstance(span, NoOpSpan):
        return False

    body = _find_streaming_body(parsed)
    if body is None:
        return False

    streaming_spans = _StreamingBodySpans(span, body, ORIGIN)
    read_in_progress = False

    def sentry_streaming_body_read(*args: "Any", **kwargs: "Any") -> bytes:
        nonlocal read_in_progress
        read_in_progress = True
        try:
            read_return_value = orig_read(*args, **kwargs)
            with capture_internal_exceptions():
                streaming_spans.finish_if_read(
                    args[0] if args else kwargs.get("amt"), read_return_value
                )
            return read_return_value
        except BaseException as error:
            streaming_spans.finish(error)
            raise
        finally:
            read_in_progress = False

    def sentry_streaming_body_close(*args: "Any", **kwargs: "Any") -> None:
        try:
            orig_close(*args, **kwargs)
            streaming_spans.finish()
        except BaseException as error:
            streaming_spans.finish(error)
            raise

    def sentry_raw_stream_close(*args: "Any", **kwargs: "Any") -> None:
        try:
            orig_raw_close(*args, **kwargs)
            if not read_in_progress:
                streaming_spans.finish()
        except BaseException as error:
            streaming_spans.finish(error)
            raise

    try:
        orig_read = body.read
        orig_close = body.close
        raw_stream = body._raw_stream  # type: ignore[attr-defined]
        orig_raw_close = raw_stream.close

        raw_stream.close = sentry_raw_stream_close
        body.read = sentry_streaming_body_read  # type: ignore
        body.close = sentry_streaming_body_close  # type: ignore
    except Exception:
        streaming_spans.finish()
        raise

    return True


def _set_request_attributes(span: "Span", request: "AWSRequest") -> None:
    client = sentry_sdk.get_client()

    parsed_url = None
    with capture_internal_exceptions():
        parsed_url = parse_url(cast(str, request.url), sanitize=False)

    # overwrite server attributes when actual request URL is resolved.
    span.set_attributes(_get_server_attributes(request.url))

    span.set_attributes(get_url_attributes(client, parsed_url))
    span.set_attribute(SPANDATA.HTTP_REQUEST_METHOD, cast(str, request.method))


def _add_request_breadcrumb(request: "AWSRequest") -> None:
    client = sentry_sdk.get_client()

    parsed_url = None
    with capture_internal_exceptions():
        parsed_url = parse_url(cast(str, request.url), sanitize=False)

    breadcrumb: "dict[str, Any]" = {}

    breadcrumb.update(get_url_attributes(client, parsed_url))
    breadcrumb[SPANDATA.HTTP_REQUEST_METHOD] = request.method

    add_http_breadcrumb(None, breadcrumb)


def _sentry_request_created(
    request: "AWSRequest", operation_name: str, **kwargs: "Any"
) -> None:
    """
    Enrich a single `AWSRequest` attempt. Botocore creates a
    fresh `AWSRequest` on every retry.
    https://github.com/boto/botocore/blob/f9195c79ea2bf46350dd320d2a0bf3db7da0b460/botocore/endpoint.py#L178-L202
    """

    if not _is_enabled():
        return

    with capture_internal_exceptions():
        _add_request_breadcrumb(request)

        span = sentry_sdk.get_current_span()
        if span is None:
            return

        # an ignored span is not active; avoid enriching its parent.
        if span.active:
            return

        _set_request_attributes(span, request)
        # each attempt has a fresh `request.context`; carry the active client span.
        request.context["_sentrysdk_span"] = span


def _sentry_before_sign(
    request: "AWSRequest", signature_version: "Any", **kwargs: "Any"
) -> None:
    if not _is_enabled():
        return

    client = sentry_sdk.get_client()
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
