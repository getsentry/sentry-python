from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from botocore.awsrequest import AWSRequest
from botocore.response import StreamingBody

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
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

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext

_AWS_RPC_SYSTEM_NAME = "aws-api"


def _set_span_attributes(
    span: "Union[Span, StreamedSpan]", attributes: "Attributes"
) -> None:
    # streamed and legacy spans expose different attribute APIs.
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


def _get_client_attributes(
    ctx: "AwsCallContext",
) -> "Attributes":
    # The AWS SDK conventions define `rpc.service` as the modeled AWS service ID
    # and `rpc.method` as the modeled operation name. Although the general RPC
    # conventions now deprecate `rpc.service`, the AWS-specific convention still
    # recommends both attributes and defines the span name as `Service.Operation`.
    # https://opentelemetry.io/docs/specs/semconv/cloud-providers/aws-sdk/#aws-sdk-spans
    attributes: "Attributes" = {
        SPANDATA.RPC_METHOD: ctx.operation_name,
        SPANDATA.RPC_SERVICE: ctx.service_id,
        SPANDATA.RPC_SYSTEM_NAME: _AWS_RPC_SYSTEM_NAME,
    }

    if ctx.region_name:
        attributes[SPANDATA.CLOUD_REGION] = ctx.region_name

    attributes.update(_get_server_attributes(ctx.endpoint_url))
    return attributes


def _start_client_span(
    ctx: "AwsCallContext",
) -> "Optional[Union[Span, StreamedSpan]]":
    client = sentry_sdk.get_client()
    if client.get_integration(Boto3Integration) is None:
        return None

    # AWS client spans use `Service.Operation`, e.g. `DynamoDB.GetItem`.
    # https://opentelemetry.io/docs/specs/semconv/cloud-providers/aws-sdk/#aws-sdk-spans
    span_name = "%s.%s" % (ctx.service_id, ctx.operation_name)
    attributes = _get_client_attributes(ctx)
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
    _set_span_attributes(span, attributes)
    span.set_tag("aws.service_id", ctx.service_id_hyphenized)
    span.set_tag("aws.operation_name", ctx.operation_name)
    return span


def _finish_client_span(
    span: "Union[Span, StreamedSpan]",
    parsed: "Dict[str, Any]",
) -> None:
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
        except Exception:
            if isinstance(streaming_span, StreamedSpan):
                streaming_span.end()
            else:
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
