import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sentry_sdk._batcher import Batcher
from sentry_sdk.consts import SPANSTATUS
from sentry_sdk.envelope import Envelope, Item, PayloadRef
from sentry_sdk.utils import format_timestamp, serialize_attribute, safe_repr

if TYPE_CHECKING:
    from typing import Any, Callable, Optional
    from sentry_sdk.traces import StreamedSpan
    from sentry_sdk._types import SerializedAttributeValue


class SpanBatcher(Batcher["StreamedSpan"]):
    # TODO[span-first]: size-based flushes
    # TODO[span-first]: adjust flush/drop defaults
    MAX_BEFORE_FLUSH = 1000
    MAX_BEFORE_DROP = 5000
    FLUSH_WAIT_TIME = 5.0

    TYPE = "span"
    CONTENT_TYPE = "application/vnd.sentry.items.span.v2+json"

    def __init__(
        self,
        capture_func: "Callable[[Envelope], None]",
        record_lost_func: "Callable[..., None]",
    ) -> None:
        # Spans from different traces cannot be emitted in the same envelope
        # since the envelope contains a shared trace header. That's why we bucket
        # by trace_id, so that we can then send the buckets each in its own
        # envelope.
        # trace_id -> span buffer
        self._span_buffer: dict[str, list["StreamedSpan"]] = defaultdict(list)
        self._capture_func = capture_func
        self._record_lost_func = record_lost_func
        self._running = True
        self._lock = threading.Lock()

        self._flush_event: "threading.Event" = threading.Event()

        self._flusher: "Optional[threading.Thread]" = None
        self._flusher_pid: "Optional[int]" = None

    def get_size(self) -> int:
        # caller is responsible for locking before checking this
        return sum(len(buffer) for buffer in self._span_buffer.values())

    def add(self, span: "StreamedSpan") -> None:
        if not self._ensure_thread() or self._flusher is None:
            return None

        with self._lock:
            size = self.get_size()
            if size >= self.MAX_BEFORE_DROP:
                self._record_lost_func(
                    reason="queue_overflow",
                    data_category="span",
                    quantity=1,
                )
                return None

            self._span_buffer[span.trace_id].append(span)
            if size + 1 >= self.MAX_BEFORE_FLUSH:
                self._flush_event.set()

    @staticmethod
    def _to_transport_format(item: "StreamedSpan") -> "Any":
        # TODO[span-first]
        res: "dict[str, Any]" = {
            "span_id": item.span_id,
            "name": item._name,
            "status": item._status,
        }

        if item._attributes:
            res["attributes"] = {
                k: serialize_attribute(v) for (k, v) in item._attributes.items()
            }

        return res

    def _flush(self) -> None:
        with self._lock:
            if len(self._span_buffer) == 0:
                return None

            envelopes = []
            for trace_id, spans in self._span_buffer.items():
                if spans:
                    # TODO[span-first]
                    # dsc = spans[0].dynamic_sampling_context()
                    dsc = None

                    envelope = Envelope(
                        headers={
                            "sent_at": format_timestamp(datetime.now(timezone.utc)),
                            "trace": dsc,
                        }
                    )

                    envelope.add_item(
                        Item(
                            type="span",
                            content_type="application/vnd.sentry.items.span.v2+json",
                            headers={
                                "item_count": len(spans),
                            },
                            payload=PayloadRef(
                                json={
                                    "items": [
                                        self._to_transport_format(span)
                                        for span in spans
                                    ]
                                }
                            ),
                        )
                    )

                    envelopes.append(envelope)

            self._span_buffer.clear()

        for envelope in envelopes:
            self._capture_func(envelope)
