import os
import random
import threading
import time
import weakref
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
    # a bit of a buffer for spans that appear between the trigger to flush
    # and actually flushing the buffer.
    #
    # The max limits are all per trace (per bucket).
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

        self._last_full_flush: float = time.monotonic()  # drives time-based flushes
        self._flush_event = threading.Event()
        self._pending_flush: set[str] = set()  # buckets to be flushed

        self._flusher: "Optional[threading.Thread]" = None
        self._flusher_pid: "Optional[int]" = None

        # See https://github.com/getsentry/sentry-python/blob/051cc01640a29bfd64b1f1e2e3414c02f027dd1b/sentry_sdk/monitor.py#L41-L50
        if hasattr(os, "register_at_fork"):
            weak_reset = weakref.WeakMethod(self._reset_thread_state)

            def _reset_in_child() -> None:
                method = weak_reset()
                if method is not None:
                    method()

            os.register_at_fork(after_in_child=_reset_in_child)

    def _reset_thread_state(self) -> None:
        self._span_buffer = defaultdict(list)
        self._running_size = defaultdict(lambda: 0)
        self._running = True

        self._lock = threading.Lock()
        self._active = threading.local()

        self._last_full_flush = time.monotonic()
        self._flush_event = threading.Event()
        self._pending_flush = set()

        self._flusher = None
        self._flusher_pid = None

    def _flush_loop(self) -> None:
        self._active.flag = True
        while self._running:
            jitter = random.random() * self.FLUSH_WAIT_TIME * 0.1
            self._flush_event.wait(timeout=self.FLUSH_WAIT_TIME + jitter)
            self._flush_event.clear()

            self._flush(only_pending=True)

            if (
                time.monotonic() - self._last_full_flush
                >= self.FLUSH_WAIT_TIME + jitter
            ):
                self._flush()
                self._last_full_flush = time.monotonic()

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

                if (
                    size + 1 >= self.MAX_BEFORE_FLUSH
                    or self._running_size[span.trace_id] >= self.MAX_BYTES_BEFORE_FLUSH
                ):
                    self._pending_flush.add(span.trace_id)
                    notify = True
                else:
                    notify = False

            if notify:
                self._flush_event.set()
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
            "name": item._name if item._name is not None else "<unlabeled span>",
            "status": item._status,
            "is_segment": item._is_segment(),
            "start_timestamp": item._start_timestamp.timestamp(),
        }

        if item._end_timestamp:
            res["end_timestamp"] = item._end_timestamp.timestamp()

        if item._parent_span_id:
            res["parent_span_id"] = item._parent_span_id

        if item._attributes:
            res["attributes"] = {
                k: serialize_attribute(v) for (k, v) in item._attributes.items()
            }

        return res

    def _flush(self, only_pending: bool = False) -> None:
        with self._lock:
            if only_pending:
                buckets = list(self._pending_flush)
            else:
                # flush whole buffer, e.g. if the SDK is shutting down
                buckets = list(self._span_buffer.keys())

            self._pending_flush.clear()

            if not buckets:
                return

            envelopes = []

            for bucket_id in buckets:
                spans = self._span_buffer.get(bucket_id)
                if not spans:
                    continue

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
                                    "version": 2,
                                    "items": [
                                        self._to_transport_format(spans[j])
                                        for j in range(start, end)
                                    ],
                                }
                            ),
                        )
                    )

                    envelopes.append(envelope)

                del self._span_buffer[bucket_id]
                del self._running_size[bucket_id]

        for envelope in envelopes:
            self._capture_func(envelope)
