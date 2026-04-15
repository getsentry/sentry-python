import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sentry_sdk._batcher import Batcher
from sentry_sdk.envelope import Envelope, Item, PayloadRef
from sentry_sdk.utils import format_timestamp, serialize_attribute

if TYPE_CHECKING:
    from typing import Any, Callable, Optional
    from sentry_sdk.traces import StreamedSpan


class SpanBatcher(Batcher["StreamedSpan"]):
    # MAX_BEFORE_FLUSH should be lower than MAX_BEFORE_DROP, so that there is
    # a bit of a buffer for spans that appear between setting the flush event
    # and actually flushing the buffer.
    #
    # The max limits are all per trace.
    MAX_ENVELOPE_SIZE = 1000  # spans
    MAX_BEFORE_FLUSH = 1000
    MAX_BEFORE_DROP = 2000
    MAX_BYTES_BEFORE_FLUSH = 5 * 1024 * 1024  # 5 MB
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
        self._running_size: dict[str, int] = defaultdict(lambda: 0)
        self._capture_func = capture_func
        self._record_lost_func = record_lost_func
        self._running = True
        self._lock = threading.Lock()
        self._active: "threading.local" = threading.local()

        self._flush_event: "threading.Event" = threading.Event()

        self._flusher: "Optional[threading.Thread]" = None
        self._flusher_pid: "Optional[int]" = None

    def add(self, span: "StreamedSpan") -> None:
        # Bail out if the current thread is already executing batcher code.
        # This prevents deadlocks when code running inside the batcher (e.g.
        # _add_to_envelope during flush, or _flush_event.wait/set) triggers
        # a GC-emitted warning that routes back through the logging
        # integration into add().
        if getattr(self._active, "flag", False):
            return None

        self._active.flag = True

        try:
            if not self._ensure_thread() or self._flusher is None:
                return None

            with self._lock:
                size = len(self._span_buffer[span.trace_id])
                if size >= self.MAX_BEFORE_DROP:
                    self._record_lost_func(
                        reason="queue_overflow",
                        data_category="span",
                        quantity=1,
                    )
                    return None

                self._span_buffer[span.trace_id].append(span)
                self._running_size[span.trace_id] += self._estimate_size(span)

                if size + 1 >= self.MAX_BEFORE_FLUSH:
                    self._flush_event.set()
                    return

                if self._running_size[span.trace_id] >= self.MAX_BYTES_BEFORE_FLUSH:
                    self._flush_event.set()
                    return
        finally:
            self._active.flag = False

    @staticmethod
    def _estimate_size(item: "StreamedSpan") -> int:
        # Rough estimate of serialized span size that's quick to compute.
        # 210 is the rough size of the payload without attributes, and then we
        # estimate the attributes separately.
        estimate = 210
        for value in item._attributes.values():
            estimate += 50

            if isinstance(value, str):
                estimate += len(value)
            else:
                estimate += len(str(value))

        return estimate

    @staticmethod
    def _to_transport_format(item: "StreamedSpan") -> "Any":
        res: "dict[str, Any]" = {
            "trace_id": item.trace_id,
            "span_id": item.span_id,
            "name": item._name,
            "status": item._status,
            "is_segment": item._is_segment(),
            "start_timestamp": item._start_timestamp.timestamp(),
        }

        if item._timestamp:
            res["end_timestamp"] = item._timestamp.timestamp()

        if item._parent_span_id:
            res["parent_span_id"] = item._parent_span_id

        if item._attributes:
            res["attributes"] = {
                k: serialize_attribute(v) for (k, v) in item._attributes.items()
            }

        return res

    def _flush(self) -> None:
        with self._lock:
            if len(self._span_buffer) == 0:
                return

            envelopes = []
            for spans in self._span_buffer.values():
                if spans:
                    dsc = spans[0]._dynamic_sampling_context()

                    # Max per envelope is 1000, so if we happen to have more than
                    # 1000 spans in one bucket, we'll need to separate them.
                    for start in range(0, len(spans), self.MAX_ENVELOPE_SIZE):
                        end = min(start + self.MAX_ENVELOPE_SIZE, len(spans))

                        envelope = Envelope(
                            headers={
                                "sent_at": format_timestamp(datetime.now(timezone.utc)),
                                "trace": dsc,
                            }
                        )

                        envelope.add_item(
                            Item(
                                type=self.TYPE,
                                content_type=self.CONTENT_TYPE,
                                headers={
                                    "item_count": end - start,
                                },
                                payload=PayloadRef(
                                    json={
                                        "items": [
                                            self._to_transport_format(spans[j])
                                            for j in range(start, end)
                                        ]
                                    }
                                ),
                            )
                        )

                        envelopes.append(envelope)

            self._span_buffer.clear()
            self._running_size.clear()

        for envelope in envelopes:
            self._capture_func(envelope)
