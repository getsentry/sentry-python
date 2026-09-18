from typing import TYPE_CHECKING

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
    from typing import Any, Dict, Optional, Type, Union

    from botocore.model import ServiceId


try:
    from botocore.awsrequest import AWSRequest
    from botocore.response import StreamingBody
except ImportError:
    raise DidNotEnable("botocore is not installed")


def _sentry_request_created(
    service_id: "ServiceId", request: "AWSRequest", operation_name: str, **kwargs: "Any"
) -> None:
    description = "aws.%s.%s" % (service_id.hyphenize(), operation_name)

    client = sentry_sdk.get_client()
    if client.get_integration(Boto3Integration) is None:
        return

    parsed_url = None
    if request.url is not None:
        with capture_internal_exceptions():
            parsed_url = parse_url(request.url, sanitize=False)

    breadcrumb: "dict[str, Any]" = {}

    is_span_streaming_enabled = has_span_streaming_enabled(client.options)
    span: "Union[Span, StreamedSpan, None]" = None
    if is_span_streaming_enabled:
        url_attributes = get_url_attributes(client, parsed_url)
        breadcrumb.update(url_attributes)

        if request.method is not None:
            breadcrumb[SPANDATA.HTTP_REQUEST_METHOD] = request.method

        if sentry_sdk.traces.get_current_span() is not None:
            span = sentry_sdk.traces.start_span(
                name=description,
                attributes={
                    "sentry.op": OP.HTTP_CLIENT,
                    "sentry.origin": Boto3Integration.origin,
                    SPANDATA.RPC_METHOD: f"{service_id}/{operation_name}",
                },
            )
            span.set_attributes(url_attributes)

            if request.method is not None:
                span.set_attribute(SPANDATA.HTTP_REQUEST_METHOD, request.method)
    else:
        span = sentry_sdk.start_span(
            op=OP.HTTP_CLIENT,
            name=description,
            origin=Boto3Integration.origin,
        )

        if parsed_url:
            span.set_data("aws.request.url", parsed_url.url)
            span.set_data(SPANDATA.HTTP_QUERY, parsed_url.query)
            span.set_data(SPANDATA.HTTP_FRAGMENT, parsed_url.fragment)
            breadcrumb.update(
                {
                    "aws.request.url": parsed_url.url,
                    SPANDATA.HTTP_QUERY: parsed_url.query,
                    SPANDATA.HTTP_FRAGMENT: parsed_url.fragment,
                }
            )

        span.set_tag("aws.service_id", service_id.hyphenize())
        span.set_tag("aws.operation_name", operation_name)
        if request.method is not None:
            span.set_data(SPANDATA.HTTP_METHOD, request.method)
            breadcrumb[SPANDATA.HTTP_METHOD] = request.method

        # We do it in order for subsequent http calls/retries be
        # attached to this span.
        span.__enter__()

    add_http_breadcrumb(None, breadcrumb)

    if span is not None:
        # request.context is an open-ended data-structure
        # where we can add anything useful in request life cycle.
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
            # preserve third-party baggage, replace stale `sentry-*` values
            add_sentry_baggage_to_headers(combined_baggage, header_value)
            _replace_header(
                request, BAGGAGE_HEADER_NAME, combined_baggage[BAGGAGE_HEADER_NAME]
            )


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
                "sentry.op": OP.HTTP_CLIENT_STREAM,
                "sentry.origin": Boto3Integration.origin,
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
        _finish_span(streaming_span, error)

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


def _sentry_after_call(
    context: "Dict[str, Any]", parsed: "Dict[str, Any]", **kwargs: "Any"
) -> None:
    span: "Optional[Union[Span, StreamedSpan]]" = context.pop("_sentrysdk_span", None)

    # Span could be absent if the integration is disabled.
    if span is None:
        return

    span.__exit__(None, None, None)

    with capture_internal_exceptions():
        _instrument_streaming_body(span, parsed)


def _sentry_after_call_error(
    context: "Dict[str, Any]", exception: "Type[BaseException]", **kwargs: "Any"
) -> None:
    span: "Optional[Union[Span, StreamedSpan]]" = context.pop("_sentrysdk_span", None)

    # Span could be absent if the integration is disabled.
    if span is None:
        return

    span.__exit__(type(exception), exception, None)
