from __future__ import annotations
import re
from datetime import datetime, timezone
from dataclasses import dataclass

from urllib3.util import parse_url as urlparse
from urllib.parse import quote, unquote
from opentelemetry.trace import (
    Span as AbstractSpan,
    SpanKind,
    StatusCode,
    format_trace_id,
    format_span_id,
    TraceState,
)
from opentelemetry.semconv.trace import SpanAttributes
from opentelemetry.sdk.trace import ReadableSpan

import sentry_sdk
from sentry_sdk.utils import Dsn
from sentry_sdk.consts import (
    SPANSTATUS,
    OP,
    SPANDATA,
    DEFAULT_SPAN_ORIGIN,
    LOW_QUALITY_TRANSACTION_SOURCES,
)
from sentry_sdk.opentelemetry.consts import SentrySpanAttribute
from sentry_sdk.tracing_utils import Baggage, get_span_status_from_http_code

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional, Union, Type, TypeVar
    from opentelemetry.util.types import Attributes

    T = TypeVar("T")


GRPC_ERROR_MAP = {
    "1": SPANSTATUS.CANCELLED,
    "2": SPANSTATUS.UNKNOWN_ERROR,
    "3": SPANSTATUS.INVALID_ARGUMENT,
    "4": SPANSTATUS.DEADLINE_EXCEEDED,
    "5": SPANSTATUS.NOT_FOUND,
    "6": SPANSTATUS.ALREADY_EXISTS,
    "7": SPANSTATUS.PERMISSION_DENIED,
    "8": SPANSTATUS.RESOURCE_EXHAUSTED,
    "9": SPANSTATUS.FAILED_PRECONDITION,
    "10": SPANSTATUS.ABORTED,
    "11": SPANSTATUS.OUT_OF_RANGE,
    "12": SPANSTATUS.UNIMPLEMENTED,
    "13": SPANSTATUS.INTERNAL_ERROR,
    "14": SPANSTATUS.UNAVAILABLE,
    "15": SPANSTATUS.DATA_LOSS,
    "16": SPANSTATUS.UNAUTHENTICATED,
}


def is_sentry_span(span: ReadableSpan) -> bool:
    """
    Break infinite loop:
    HTTP requests to Sentry are caught by OTel and send again to Sentry.
    """
    from sentry_sdk import get_client

    if not span.attributes:
        return False

    span_url = get_typed_attribute(span.attributes, SpanAttributes.HTTP_URL, str)
    if span_url is None:
        return False

    dsn_url = None
    client = get_client()

    if client.dsn:
        try:
            dsn_url = Dsn(client.dsn).netloc
        except Exception:
            pass

    if not dsn_url:
        return False

    if dsn_url in span_url:
        return True

    return False


def convert_from_otel_timestamp(time: int) -> datetime:
    """Convert an OTel nanosecond-level timestamp to a datetime."""
    return datetime.fromtimestamp(time / 1e9, timezone.utc)


def convert_to_otel_timestamp(time: Union[datetime, float]) -> int:
    """Convert a datetime to an OTel timestamp (with nanosecond precision)."""
    if isinstance(time, datetime):
        return int(time.timestamp() * 1e9)
    return int(time * 1e9)


def extract_transaction_name_source(
    span: ReadableSpan,
) -> tuple[Optional[str], Optional[str]]:
    if not span.attributes:
        return (None, None)
    return (
        get_typed_attribute(span.attributes, SentrySpanAttribute.NAME, str),
        get_typed_attribute(span.attributes, SentrySpanAttribute.SOURCE, str),
    )


@dataclass
class ExtractedSpanData:
    description: str
    op: Optional[str] = None
    status: Optional[str] = None
    http_status: Optional[int] = None
    origin: Optional[str] = None


def extract_span_data(span: ReadableSpan) -> ExtractedSpanData:
    """
    Try to populate sane values for op, description and statuses based on what we have.
    The op and description mapping is fundamentally janky because otel only has a single `name`.

    Priority is given first to attributes explicitly defined by us via the SDK.
    Otherwise we try to infer sane values from other attributes.
    """
    op = get_typed_attribute(span.attributes, SentrySpanAttribute.OP, str) or infer_op(
        span
    )

    description = (
        get_typed_attribute(span.attributes, SentrySpanAttribute.DESCRIPTION, str)
        or get_typed_attribute(span.attributes, SentrySpanAttribute.NAME, str)
        or infer_description(span)
    )

    origin = get_typed_attribute(span.attributes, SentrySpanAttribute.ORIGIN, str)

    (status, http_status) = extract_span_status(span)

    return ExtractedSpanData(
        description=description or span.name,
        op=op,
        status=status,
        http_status=http_status,
        origin=origin,
    )


def infer_op(span: ReadableSpan) -> Optional[str]:
    """
    Try to infer op for the various types of instrumentation.
    """
    if span.attributes is None:
        return None

    if SpanAttributes.HTTP_METHOD in span.attributes:
        op = "http"
        if span.kind == SpanKind.SERVER:
            op += ".server"
        elif span.kind == SpanKind.CLIENT:
            op += ".client"
        return op
    elif SpanAttributes.DB_SYSTEM in span.attributes:
        return OP.DB
    elif SpanAttributes.RPC_SERVICE in span.attributes:
        return OP.RPC
    elif SpanAttributes.MESSAGING_SYSTEM in span.attributes:
        return OP.MESSAGE
    elif SpanAttributes.FAAS_TRIGGER in span.attributes:
        return get_typed_attribute(span.attributes, SpanAttributes.FAAS_TRIGGER, str)
    else:
        return None


def infer_description(span: ReadableSpan) -> Optional[str]:
    if span.attributes is None:
        return None

    if SpanAttributes.HTTP_METHOD in span.attributes:
        http_method = get_typed_attribute(
            span.attributes, SpanAttributes.HTTP_METHOD, str
        )
        route = get_typed_attribute(span.attributes, SpanAttributes.HTTP_ROUTE, str)
        target = get_typed_attribute(span.attributes, SpanAttributes.HTTP_TARGET, str)
        peer_name = get_typed_attribute(
            span.attributes, SpanAttributes.NET_PEER_NAME, str
        )
        url = get_typed_attribute(span.attributes, SpanAttributes.HTTP_URL, str)

        if route:
            return f"{http_method} {route}"
        elif target:
            return f"{http_method} {target}"
        elif peer_name:
            return f"{http_method} {peer_name}"
        elif url:
            parsed_url = urlparse(url)
            url = "{}://{}{}".format(
                parsed_url.scheme, parsed_url.netloc, parsed_url.path
            )
            return f"{http_method} {url}"
        else:
            return http_method
    elif SpanAttributes.DB_SYSTEM in span.attributes:
        return get_typed_attribute(span.attributes, SpanAttributes.DB_STATEMENT, str)
    else:
        return None


def extract_span_status(span: ReadableSpan) -> tuple[Optional[str], Optional[int]]:
    """
    Extract a reasonable Sentry SPANSTATUS and a HTTP status code from the otel span.
    OKs are simply OKs.
    ERRORs first try to map to HTTP/GRPC statuses via attributes otherwise fallback
    on the description if it is a valid status for Sentry.
    In the final UNSET case, we try to infer HTTP/GRPC.
    """
    status = span.status
    http_status = get_http_status_code(span.attributes)
    final_status = None

    if status.status_code == StatusCode.OK:
        final_status = SPANSTATUS.OK
    elif status.status_code == StatusCode.ERROR:
        inferred_status = infer_status_from_attributes(span.attributes, http_status)

        if inferred_status is not None:
            final_status = inferred_status
        elif (
            status.description is not None
            and status.description in GRPC_ERROR_MAP.values()
        ):
            final_status = status.description
        else:
            final_status = SPANSTATUS.UNKNOWN_ERROR
    else:
        # UNSET case
        final_status = infer_status_from_attributes(span.attributes, http_status)

    return (final_status, http_status)


def infer_status_from_attributes(
    span_attributes: Attributes, http_status: Optional[int]
) -> Optional[str]:
    if span_attributes is None:
        return None

    if http_status:
        return get_span_status_from_http_code(http_status)

    grpc_status = span_attributes.get(SpanAttributes.RPC_GRPC_STATUS_CODE)
    if grpc_status:
        return GRPC_ERROR_MAP.get(str(grpc_status), SPANSTATUS.UNKNOWN_ERROR)

    return None


def get_http_status_code(span_attributes: Attributes) -> Optional[int]:
    try:
        http_status = get_typed_attribute(
            span_attributes, SpanAttributes.HTTP_RESPONSE_STATUS_CODE, int
        )
    except AttributeError:
        # HTTP_RESPONSE_STATUS_CODE was added in 1.21, so if we're on an older
        # OTel version SpanAttributes.HTTP_RESPONSE_STATUS_CODE will throw an
        # AttributeError
        http_status = None

    if http_status is None:
        # Fall back to the deprecated attribute
        http_status = get_typed_attribute(
            span_attributes, SpanAttributes.HTTP_STATUS_CODE, int
        )

    return http_status


def extract_span_attributes(span: ReadableSpan, namespace: str) -> dict[str, Any]:
    """
    Extract Sentry-specific span attributes and make them look the way Sentry expects.
    """
    extracted_attrs: dict[str, Any] = {}

    for attr, value in (span.attributes or {}).items():
        if attr.startswith(namespace):
            key = attr[len(namespace) + 1 :]
            extracted_attrs[key] = value

    return extracted_attrs


def get_trace_context(
    span: ReadableSpan, span_data: Optional[ExtractedSpanData] = None
) -> dict[str, Any]:
    if not span.context:
        return {}

    trace_id = format_trace_id(span.context.trace_id)
    span_id = format_span_id(span.context.span_id)
    parent_span_id = format_span_id(span.parent.span_id) if span.parent else None

    if span_data is None:
        span_data = extract_span_data(span)

    trace_context: dict[str, Any] = {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "origin": span_data.origin or DEFAULT_SPAN_ORIGIN,
    }

    if span_data.op:
        trace_context["op"] = span_data.op
    if span_data.status:
        trace_context["status"] = span_data.status
    if span.attributes:
        trace_context["data"] = dict(span.attributes)

    trace_state = get_trace_state(span)
    trace_context["dynamic_sampling_context"] = dsc_from_trace_state(trace_state)

    return trace_context


def trace_state_from_baggage(baggage: Baggage) -> TraceState:
    items = []
    for k, v in baggage.sentry_items.items():
        key = Baggage.SENTRY_PREFIX + quote(k)
        val = quote(str(v))
        items.append((key, val))
    return TraceState(items)


def baggage_from_trace_state(trace_state: TraceState) -> Baggage:
    return Baggage(dsc_from_trace_state(trace_state))


def serialize_trace_state(trace_state: TraceState) -> str:
    sentry_items = []
    for k, v in trace_state.items():
        if Baggage.SENTRY_PREFIX_REGEX.match(k):
            sentry_items.append((k, v))
    return ",".join(key + "=" + value for key, value in sentry_items)


def dsc_from_trace_state(trace_state: TraceState) -> dict[str, str]:
    dsc = {}
    for k, v in trace_state.items():
        if Baggage.SENTRY_PREFIX_REGEX.match(k):
            key = re.sub(Baggage.SENTRY_PREFIX_REGEX, "", k)
            dsc[unquote(key)] = unquote(v)
    return dsc


def has_incoming_trace(trace_state: TraceState) -> bool:
    """
    The existence of a sentry-trace_id in the baggage implies we continued an upstream trace.
    """
    return (Baggage.SENTRY_PREFIX + "trace_id") in trace_state


def get_trace_state(span: Union[AbstractSpan, ReadableSpan]) -> TraceState:
    """
    Get the existing trace_state with sentry items
    or populate it if we are the head SDK.
    """
    span_context = span.get_span_context()
    if not span_context:
        return TraceState()

    trace_state = span_context.trace_state

    if has_incoming_trace(trace_state):
        return trace_state
    else:
        client = sentry_sdk.get_client()
        if not client.is_active():
            return trace_state

        options = client.options or {}

        trace_state = trace_state.update(
            Baggage.SENTRY_PREFIX + "trace_id",
            quote(format_trace_id(span_context.trace_id)),
        )

        if options.get("environment"):
            trace_state = trace_state.update(
                Baggage.SENTRY_PREFIX + "environment", quote(options["environment"])
            )

        if options.get("release"):
            trace_state = trace_state.update(
                Baggage.SENTRY_PREFIX + "release", quote(options["release"])
            )

        if options.get("dsn"):
            trace_state = trace_state.update(
                Baggage.SENTRY_PREFIX + "public_key",
                quote(Dsn(options["dsn"]).public_key),
            )

        root_span = get_sentry_meta(span, "root_span")
        if root_span and isinstance(root_span, ReadableSpan):
            transaction_name, transaction_source = extract_transaction_name_source(
                root_span
            )

            if (
                transaction_name
                and transaction_source not in LOW_QUALITY_TRANSACTION_SOURCES
            ):
                trace_state = trace_state.update(
                    Baggage.SENTRY_PREFIX + "transaction", quote(transaction_name)
                )

        return trace_state


def get_sentry_meta(span: Union[AbstractSpan, ReadableSpan], key: str) -> Any:
    sentry_meta = getattr(span, "_sentry_meta", None)
    return sentry_meta.get(key) if sentry_meta else None


def set_sentry_meta(
    span: Union[AbstractSpan, ReadableSpan], key: str, value: Any
) -> None:
    sentry_meta = getattr(span, "_sentry_meta", {})
    sentry_meta[key] = value
    span._sentry_meta = sentry_meta  # type: ignore[union-attr]


def delete_sentry_meta(span: Union[AbstractSpan, ReadableSpan]) -> None:
    try:
        del span._sentry_meta  # type: ignore[union-attr]
    except AttributeError:
        pass


def get_profile_context(span: ReadableSpan) -> Optional[dict[str, str]]:
    if not span.attributes:
        return None

    profiler_id = get_typed_attribute(span.attributes, SPANDATA.PROFILER_ID, str)
    if profiler_id is None:
        return None

    return {"profiler_id": profiler_id}


def get_typed_attribute(attributes: Attributes, key: str, type: Type[T]) -> Optional[T]:
    """
    helper method to coerce types of attribute values
    """
    if attributes is None:
        return None
    value = attributes.get(key)
    if value is not None and isinstance(value, type):
        return value
    else:
        return None
