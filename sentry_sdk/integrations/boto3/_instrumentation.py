from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA, SPANSTATUS
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.boto3.consts import ORIGIN
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
    from botocore.response import StreamingBody
except ImportError:
    raise DidNotEnable("botocore not installed")


def _start_client_span(
    ctx: "AwsCallContext",
) -> "Optional[Union[Span, StreamedSpan]]":
    from sentry_sdk.integrations.boto3 import Boto3Integration

    client = sentry_sdk.get_client()
    if client.get_integration(Boto3Integration) is None:
        return None

    # use unknown if `service_id_hyphenized` so span name can still be created.
    # e.g. "aws.unkown.GetObject"
    service_name = ctx.service_id_hyphenized or "unknown"
    span_name = f"aws.{service_name}.{ctx.operation_name}"

    if has_span_streaming_enabled(client.options):
        if sentry_sdk.traces.get_current_span() is None:
            return None

        attributes: "Attributes" = {
            SPANDATA.SENTRY_OP: OP.HTTP_CLIENT,
            SPANDATA.SENTRY_ORIGIN: ORIGIN,
        }
        if ctx.service_id:
            attributes[SPANDATA.RPC_METHOD] = f"{ctx.service_id}/{ctx.operation_name}"
        return sentry_sdk.traces.start_span(
            name=span_name,
            attributes=attributes,
            # `StreamingBody` responses outlive `_make_api_call()`. `_activate_client_span()`
            # activates this span only while the call itself runs.
            active=False,
        )

    span = sentry_sdk.start_span(
        name=span_name,
        op=OP.HTTP_CLIENT,
        origin=ORIGIN,
    )
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
            # `parent_span` is set explicitly to the boto span.
            parent_span=span,
            # avoid making the streaming span the current span on the scope since the application might
            # keep `StreamingBody` open before reading it. Otherwise: 1. when the streamingspan ends it
            # could restore the parent span on the scope, breaking the parent-child relation of newly
            # created spans; 2. newly created spans would be attached to the streaming span.
            active=False,
            attributes={
                "sentry.op": OP.HTTP_CLIENT_STREAM,
                "sentry.origin": ORIGIN,
            },
        )
    else:
        streaming_span = span.start_child(
            op=OP.HTTP_CLIENT_STREAM,
            name=span.description,
            origin=ORIGIN,
        )

    finished = False
    read_in_progress = False

    def finish_span(error: "Optional[BaseException]" = None) -> None:
        nonlocal finished
        if finished:
            return

        finished = True
        _finish_span(streaming_span, error)
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
        nonlocal read_in_progress
        read_in_progress = True
        try:
            read_return_value = orig_read(*args, **kwargs)
            with capture_internal_exceptions():
                amount_of_bytes_requested = args[0] if args else kwargs.get("amt")
                if (
                    amount_of_bytes_requested is None
                    or amount_of_bytes_requested < 0
                    or (amount_of_bytes_requested > 0 and not read_return_value)
                    or content_length_reached()
                ):
                    finish_span()
            return read_return_value
        except BaseException as error:
            finish_span(error)
            raise
        finally:
            read_in_progress = False

    def sentry_streaming_body_close(*args: "Any", **kwargs: "Any") -> None:
        try:
            orig_close(*args, **kwargs)
            finish_span()
        except BaseException as error:
            finish_span(error)
            raise

    def sentry_raw_stream_close(*args: "Any", **kwargs: "Any") -> None:
        try:
            orig_raw_close(*args, **kwargs)
            if not read_in_progress:
                finish_span()
        except BaseException as error:
            finish_span(error)
            raise

    try:
        orig_read = body.read
        orig_close = body.close
        raw_stream = body._raw_stream  # type: ignore[attr-defined]
        orig_raw_close = raw_stream.close

        # StreamingBody.__exit__ closes `_raw_stream` directly, bypassing
        # StreamingBody.close(), so both levels need to be instrumented.
        raw_stream.close = sentry_raw_stream_close
        body.read = sentry_streaming_body_read  # type: ignore
        body.close = sentry_streaming_body_close  # type: ignore
    except Exception:
        finish_span()
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
    https://github.com/boto/botocore/blob/f9195c79ea2bf46350dd320d2a0bf3db7da0b460/botocore/endpoint.py#L178-L202
    """
    from sentry_sdk.integrations.boto3 import Boto3Integration

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
        if isinstance(span, StreamedSpan):
            if not (span.get_attributes().get(SPANDATA.SENTRY_ORIGIN) == ORIGIN):
                return

        _set_request_attributes(span, request)
        # each attempt has a fresh `request.context`; carry the active client span.
        request.context["_sentrysdk_span"] = span


def _sentry_before_sign(
    request: "AWSRequest", signature_version: "Any", **kwargs: "Any"
) -> None:
    from sentry_sdk.integrations.boto3 import Boto3Integration

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
