from __future__ import annotations
import re
from datetime import datetime, timezone

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
    from typing import Any, Optional, Mapping, Sequence, Union, Type, TypeVar
    from sentry_sdk._types import OtelExtractedSpanData

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

    span_url = span.attributes.get(SpanAttributes.HTTP_URL, None)

    if span_url is None or not isinstance(span_url, str):
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


def extract_span_data(span: ReadableSpan) -> OtelExtractedSpanData:
    op = span.name
    description = span.name
    status, http_status = extract_span_status(span)
    origin = None
    if span.attributes is None:
        return (op, description, status, http_status, origin)

    attribute_op = get_typed_attribute(span.attributes, SentrySpanAttribute.OP, str)
    op = attribute_op or op
    description = (
        get_typed_attribute(span.attributes, SentrySpanAttribute.DESCRIPTION, str)
        or description
    )
    origin = get_typed_attribute(span.attributes, SentrySpanAttribute.ORIGIN, str)

    http_method = get_typed_attribute(span.attributes, SpanAttributes.HTTP_METHOD, str)
    if http_method:
        return span_data_for_http_method(span)

    db_query = span.attributes.get(SpanAttributes.DB_SYSTEM)
    if db_query:
        return span_data_for_db_query(span)

    rpc_service = span.attributes.get(SpanAttributes.RPC_SERVICE)
    if rpc_service:
        return (
            attribute_op or "rpc",
            description,
            status,
            http_status,
            origin,
        )

    messaging_system = span.attributes.get(SpanAttributes.MESSAGING_SYSTEM)
    if messaging_system:
        return (
            attribute_op or "message",
            description,
            status,
            http_status,
            origin,
        )

    faas_trigger = span.attributes.get(SpanAttributes.FAAS_TRIGGER)
    if faas_trigger:
        return (str(faas_trigger), description, status, http_status, origin)

    return (op, description, status, http_status, origin)


def span_data_for_http_method(span: ReadableSpan) -> OtelExtractedSpanData:
    span_attributes = span.attributes or {}

    op = get_typed_attribute(span_attributes, SentrySpanAttribute.OP, str)
    if op is None:
        op = "http"

        if span.kind == SpanKind.SERVER:
            op += ".server"
        elif span.kind == SpanKind.CLIENT:
            op += ".client"

    http_method = span_attributes.get(SpanAttributes.HTTP_METHOD)
    route = span_attributes.get(SpanAttributes.HTTP_ROUTE)
    target = span_attributes.get(SpanAttributes.HTTP_TARGET)
    peer_name = span_attributes.get(SpanAttributes.NET_PEER_NAME)

    # TODO-neel-potel remove description completely
    description = get_typed_attribute(
        span_attributes, SentrySpanAttribute.DESCRIPTION, str
    ) or get_typed_attribute(span_attributes, SentrySpanAttribute.NAME, str)
    if description is None:
        description = f"{http_method}"

        if route:
            description = f"{http_method} {route}"
        elif target:
            description = f"{http_method} {target}"
        elif peer_name:
            description = f"{http_method} {peer_name}"
        else:
            url = span_attributes.get(SpanAttributes.HTTP_URL)
            url = get_typed_attribute(span_attributes, SpanAttributes.HTTP_URL, str)

            if url:
                parsed_url = urlparse(url)
                url = "{}://{}{}".format(
                    parsed_url.scheme, parsed_url.netloc, parsed_url.path
                )
                description = f"{http_method} {url}"

    status, http_status = extract_span_status(span)

    origin = get_typed_attribute(span_attributes, SentrySpanAttribute.ORIGIN, str)

    return (op, description, status, http_status, origin)


def span_data_for_db_query(span: ReadableSpan) -> OtelExtractedSpanData:
    span_attributes = span.attributes or {}

    op = get_typed_attribute(span_attributes, SentrySpanAttribute.OP, str) or OP.DB
    statement = get_typed_attribute(span_attributes, SpanAttributes.DB_STATEMENT, str)

    description = statement or span.name
    origin = get_typed_attribute(span_attributes, SentrySpanAttribute.ORIGIN, str)

    return (op, description, None, None, origin)


def extract_span_status(span: ReadableSpan) -> tuple[Optional[str], Optional[int]]:
    span_attributes = span.attributes or {}
    status = span.status or None

    if status:
        inferred_status, http_status = infer_status_from_attributes(span_attributes)

        if status.status_code == StatusCode.OK:
            return (SPANSTATUS.OK, http_status)
        elif status.status_code == StatusCode.ERROR:
            if status.description is None:
                if inferred_status:
                    return (inferred_status, http_status)

            if http_status is not None:
                return (inferred_status, http_status)

            if (
                status.description is not None
                and status.description in GRPC_ERROR_MAP.values()
            ):
                return (status.description, None)
            else:
                return (SPANSTATUS.UNKNOWN_ERROR, None)

    inferred_status, http_status = infer_status_from_attributes(span_attributes)
    if inferred_status:
        return (inferred_status, http_status)

    if status and status.status_code == StatusCode.UNSET:
        return (None, None)
    else:
        return (SPANSTATUS.UNKNOWN_ERROR, None)


def infer_status_from_attributes(
    span_attributes: Mapping[
        str,
        str
        | bool
        | int
        | float
        | Sequence[str]
        | Sequence[bool]
        | Sequence[int]
        | Sequence[float],
    ],
) -> tuple[Optional[str], Optional[int]]:
    http_status = get_http_status_code(span_attributes)

    if http_status:
        return (get_span_status_from_http_code(http_status), http_status)

    grpc_status = span_attributes.get(SpanAttributes.RPC_GRPC_STATUS_CODE)
    if grpc_status:
        return (GRPC_ERROR_MAP.get(str(grpc_status), SPANSTATUS.UNKNOWN_ERROR), None)

    return (None, None)


def get_http_status_code(
    span_attributes: Mapping[
        str,
        str
        | bool
        | int
        | float
        | Sequence[str]
        | Sequence[bool]
        | Sequence[int]
        | Sequence[float],
    ],
) -> Optional[int]:
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
    span: ReadableSpan, span_data: Optional[OtelExtractedSpanData] = None
) -> dict[str, Any]:
    if not span.context:
        return {}

    trace_id = format_trace_id(span.context.trace_id)
    span_id = format_span_id(span.context.span_id)
    parent_span_id = format_span_id(span.parent.span_id) if span.parent else None

    if span_data is None:
        span_data = extract_span_data(span)

    (op, _, status, _, origin) = span_data

    trace_context: dict[str, Any] = {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "op": op,
        "origin": origin or DEFAULT_SPAN_ORIGIN,
    }

    if status:
        trace_context["status"] = status

    if span.attributes:
        trace_context["data"] = dict(span.attributes)

    trace_state = get_trace_state(span)
    trace_context["dynamic_sampling_context"] = dsc_from_trace_state(trace_state)

    # TODO-neel-potel profiler thread_id, thread_name

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


def get_typed_attribute(
    attributes: Mapping[str, Any], key: str, type: Type[T]
) -> Optional[T]:
    """
    helper method to coerce types of attribute values
    """
    value = attributes.get(key)
    if value is not None and isinstance(value, type):
        return value
    else:
        return None
