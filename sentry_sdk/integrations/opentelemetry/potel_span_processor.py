from collections import deque
from datetime import datetime

from opentelemetry.trace import INVALID_SPAN, get_current_span, format_trace_id, format_span_id
from opentelemetry.context import Context
from opentelemetry.sdk.trace import Span, ReadableSpan, SpanProcessor

from sentry_sdk import capture_event
from sentry_sdk.integrations.opentelemetry.utils import is_sentry_span, convert_otel_timestamp
from sentry_sdk.integrations.opentelemetry.consts import OTEL_SENTRY_CONTEXT, SPAN_ORIGIN
from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional, List, Any
    from sentry_sdk._types import Event


class PotelSentrySpanProcessor(SpanProcessor):  # type: ignore
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
        self._children_spans = {}  # type: dict[int, List[ReadableSpan]]

    def on_start(self, span, parent_context=None):
        # type: (Span, Optional[Context]) -> None
        pass

    def on_end(self, span):
        # type: (ReadableSpan) -> None
        if is_sentry_span(span):
            return

        # TODO-neel-potel-remote only take parent if not remote
        if span.parent:
            self._children_spans.setdefault(span.parent.span_id, []).append(span)
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

        children = self._collect_children(span)
        # TODO add converted spans
        capture_event(transaction_event)


    def _collect_children(self, span):
        # type: (ReadableSpan) -> List[ReadableSpan]
        if not span.context:
            return []

        children = []
        bfs_queue = deque()
        bfs_queue.append(span.context.span_id)

        while bfs_queue:
            parent_span_id = bfs_queue.popleft()
            node_children = self._children_spans.pop(parent_span_id, [])
            children.extend(node_children)
            bfs_queue.extend([child.context.span_id for child in node_children if child.context])

        return children

    # we construct the event from scratch here
    # and not use the current Transaction class for easier refactoring
    # TODO-neel-potel op, description, status logic
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

        trace_context = {
            "trace_id": trace_id,
            "span_id": span_id,
            "origin": SPAN_ORIGIN,
            "op": span.name, # TODO
            "status": "ok", # TODO
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
            "transaction": span.name, # TODO
            "transaction_info": {"source": "custom"}, # TODO
            "contexts": contexts,
            "start_timestamp": convert_otel_timestamp(span.start_time),
            "timestamp": convert_otel_timestamp(span.end_time),
            "spans": [],
        }  # type: Event

        return event
