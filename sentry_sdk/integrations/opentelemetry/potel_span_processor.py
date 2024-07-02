from collections import deque, defaultdict

from opentelemetry.trace import format_trace_id, format_span_id
from opentelemetry.context import Context
from opentelemetry.sdk.trace import Span, ReadableSpan, SpanProcessor

from sentry_sdk import capture_event
from sentry_sdk.integrations.opentelemetry.utils import (
    is_sentry_span,
    convert_otel_timestamp,
    extract_span_data,
)
from sentry_sdk.integrations.opentelemetry.consts import (
    OTEL_SENTRY_CONTEXT,
    SPAN_ORIGIN,
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
        pass

    def on_end(self, span):
        # type: (ReadableSpan) -> None
        if is_sentry_span(span):
            return

        # TODO-neel-potel-remote only take parent if not remote
        if span.parent:
            self._children_spans[span.parent.span_id].append(span)
        else:
            # if have a root span ending, we build a transaction and send it
            self._flush_root_span(span)

    # TODO-neel-potel not sure we need a clear like JS
    def shutdown(self):
        # type: () -> None
        pass

    # TODO-neel-potel change default? this is 30 sec
    # TODO-neel-potel call this in client.flush
    def force_flush(self, timeout_millis=30000):
        # type: (int) -> bool
        return True

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
        if not span.start_time:
            return None
        if not span.end_time:
            return None

        trace_id = format_trace_id(span.context.trace_id)
        span_id = format_span_id(span.context.span_id)
        parent_span_id = format_span_id(span.parent.span_id) if span.parent else None

        (op, description, _) = extract_span_data(span)

        trace_context = {
            "trace_id": trace_id,
            "span_id": span_id,
            "origin": SPAN_ORIGIN,
            "op": op,
            "status": "ok",  # TODO-neel-potel span status mapping
        }  # type: dict[str, Any]

        if parent_span_id:
            trace_context["parent_span_id"] = parent_span_id
        if span.attributes:
            trace_context["data"] = dict(span.attributes)

        contexts = {"trace": trace_context}
        if span.resource.attributes:
            contexts[OTEL_SENTRY_CONTEXT] = {"resource": dict(span.resource.attributes)}

        event = {
            "type": "transaction",
            "transaction": description,
            # TODO-neel-potel tx source based on integration
            "transaction_info": {"source": "custom"},
            "contexts": contexts,
            "start_timestamp": convert_otel_timestamp(span.start_time),
            "timestamp": convert_otel_timestamp(span.end_time),
        }  # type: Event

        return event

    def _span_to_json(self, span):
        # type: (ReadableSpan) -> Optional[dict[str, Any]]
        if not span.context:
            return None
        if not span.start_time:
            return None
        if not span.end_time:
            return None

        trace_id = format_trace_id(span.context.trace_id)
        span_id = format_span_id(span.context.span_id)
        parent_span_id = format_span_id(span.parent.span_id) if span.parent else None

        (op, description, _) = extract_span_data(span)

        span_json = {
            "trace_id": trace_id,
            "span_id": span_id,
            "origin": SPAN_ORIGIN,
            "op": op,
            "description": description,
            "status": "ok",  # TODO-neel-potel span status mapping
            "start_timestamp": convert_otel_timestamp(span.start_time),
            "timestamp": convert_otel_timestamp(span.end_time),
        }  # type: dict[str, Any]

        if parent_span_id:
            span_json["parent_span_id"] = parent_span_id
        if span.attributes:
            span_json["data"] = dict(span.attributes)

        return span_json
