from collections import deque, defaultdict
from typing import cast

from opentelemetry.trace import (
    format_trace_id,
    format_span_id,
    get_current_span,
    INVALID_SPAN,
    Span as AbstractSpan,
)
from opentelemetry.context import Context
from opentelemetry.sdk.trace import Span, ReadableSpan, SpanProcessor

from sentry_sdk import capture_event
from sentry_sdk.tracing import DEFAULT_SPAN_ORIGIN
from sentry_sdk.integrations.opentelemetry.utils import (
    is_sentry_span,
    convert_from_otel_timestamp,
    extract_span_attributes,
    extract_span_data,
    extract_transaction_name_source,
    get_trace_context,
    get_sentry_meta,
    set_sentry_meta,
)
from sentry_sdk.integrations.opentelemetry.consts import (
    OTEL_SENTRY_CONTEXT,
    SentrySpanAttribute,
)
from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional, List, Any, Deque, DefaultDict
    from sentry_sdk._types import Event


class PotelSentrySpanProcessor(SpanProcessor):
    """
    Converts OTel spans into Sentry spans so they can be sent to the Sentry backend.
    """

    def __new__(cls):
        # type: () -> PotelSentrySpanProcessor
        if not hasattr(cls, "instance"):
            cls.instance = super().__new__(cls)

        return cls.instance

    def __init__(self):
        # type: () -> None
        self._children_spans = defaultdict(
            list
        )  # type: DefaultDict[int, List[ReadableSpan]]

    def on_start(self, span, parent_context=None):
        # type: (Span, Optional[Context]) -> None
        if not is_sentry_span(span):
            self._add_root_span(span, get_current_span(parent_context))

    def on_end(self, span):
        # type: (ReadableSpan) -> None
        if is_sentry_span(span):
            return

        is_root_span = not (span.parent and not span.parent.is_remote)
        if is_root_span:
            # if have a root span ending, we build a transaction and send it
            self._flush_root_span(span)
        else:
            self._children_spans[span.parent.span_id].append(span)

    # TODO-neel-potel not sure we need a clear like JS
    def shutdown(self):
        # type: () -> None
        pass

    # TODO-neel-potel change default? this is 30 sec
    # TODO-neel-potel call this in client.flush
    def force_flush(self, timeout_millis=30000):
        # type: (int) -> bool
        return True

    def _add_root_span(self, span, parent_span):
        # type: (Span, AbstractSpan) -> None
        """
        This is required to make POTelSpan.root_span work
        since we can't traverse back to the root purely with otel efficiently.
        """
        if parent_span != INVALID_SPAN and not parent_span.get_span_context().is_remote:
            # child span points to parent's root or parent
            parent_root_span = get_sentry_meta(parent_span, "root_span")
            set_sentry_meta(span, "root_span", parent_root_span or parent_span)
        else:
            # root span points to itself
            set_sentry_meta(span, "root_span", span)

    def _flush_root_span(self, span):
        # type: (ReadableSpan) -> None
        transaction_event = self._root_span_to_transaction_event(span)
        if not transaction_event:
            return

        spans = []
        for child in self._collect_children(span):
            span_json = self._span_to_json(child)
            if span_json:
                spans.append(span_json)
        transaction_event["spans"] = spans
        # TODO-neel-potel sort and cutoff max spans

        capture_event(transaction_event)

    def _collect_children(self, span):
        # type: (ReadableSpan) -> List[ReadableSpan]
        if not span.context:
            return []

        children = []
        bfs_queue = deque()  # type: Deque[int]
        bfs_queue.append(span.context.span_id)

        while bfs_queue:
            parent_span_id = bfs_queue.popleft()
            node_children = self._children_spans.pop(parent_span_id, [])
            children.extend(node_children)
            bfs_queue.extend(
                [child.context.span_id for child in node_children if child.context]
            )

        return children

    # we construct the event from scratch here
    # and not use the current Transaction class for easier refactoring
    def _root_span_to_transaction_event(self, span):
        # type: (ReadableSpan) -> Optional[Event]
        if not span.context:
            return None

        event = self._common_span_transaction_attributes_as_json(span)
        if event is None:
            return None

        transaction_name, transaction_source = extract_transaction_name_source(span)
        span_data = extract_span_data(span)
        (_, description, _, http_status, _) = span_data

        trace_context = get_trace_context(span, span_data=span_data)
        contexts = {"trace": trace_context}

        if http_status:
            contexts["response"] = {"status_code": http_status}

        if span.resource.attributes:
            contexts[OTEL_SENTRY_CONTEXT] = {"resource": dict(span.resource.attributes)}

        event.update(
            {
                "type": "transaction",
                "transaction": transaction_name or description,
                "transaction_info": {"source": transaction_source or "custom"},
                "contexts": contexts,
            }
        )

        return event

    def _span_to_json(self, span):
        # type: (ReadableSpan) -> Optional[dict[str, Any]]
        if not span.context:
            return None

        # This is a safe cast because dict[str, Any] is a superset of Event
        span_json = cast(
            "dict[str, Any]", self._common_span_transaction_attributes_as_json(span)
        )
        if span_json is None:
            return None

        trace_id = format_trace_id(span.context.trace_id)
        span_id = format_span_id(span.context.span_id)
        parent_span_id = format_span_id(span.parent.span_id) if span.parent else None

        (op, description, status, _, origin) = extract_span_data(span)

        span_json.update(
            {
                "trace_id": trace_id,
                "span_id": span_id,
                "op": op,
                "description": description,
                "status": status,
                "origin": origin or DEFAULT_SPAN_ORIGIN,
            }
        )

        if parent_span_id:
            span_json["parent_span_id"] = parent_span_id

        if span.attributes:
            span_json["data"] = dict(span.attributes)

        return span_json

    def _common_span_transaction_attributes_as_json(self, span):
        # type: (ReadableSpan) -> Optional[Event]
        if not span.start_time or not span.end_time:
            return None

        common_json = {
            "start_timestamp": convert_from_otel_timestamp(span.start_time),
            "timestamp": convert_from_otel_timestamp(span.end_time),
        }  # type: Event

        measurements = extract_span_attributes(span, SentrySpanAttribute.MEASUREMENT)
        if measurements:
            common_json["measurements"] = measurements

        tags = extract_span_attributes(span, SentrySpanAttribute.TAG)
        if tags:
            common_json["tags"] = tags

        return common_json
