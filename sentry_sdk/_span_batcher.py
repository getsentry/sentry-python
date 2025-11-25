# This file is experimental and its contents may change without notice. This is
# a simple POC buffer implementation. Eventually, we should switch to a telemetry
# buffer: https://develop.sentry.dev/sdk/telemetry/telemetry-buffer/

import os
import random
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional, List, Callable, TYPE_CHECKING, Any

from sentry_sdk.consts import SPANSTATUS
from sentry_sdk.envelope import Envelope, Item, PayloadRef
from sentry_sdk.tracing import Transaction

if TYPE_CHECKING:
    from sentry_sdk.tracing import Span
    from sentry_sdk._types import SpanV2


class SpanBatcher:
    # TODO[span-first]: Adjust limits. However, there's still a restriction of
    # at most 1000 spans per envelope.
    MAX_SPANS_BEFORE_FLUSH = 1_000
    MAX_SPANS_BEFORE_DROP = 2_000
    FLUSH_WAIT_TIME = 5.0

    def __init__(
        self,
        capture_func,  # type: Callable[[Envelope], None]
        record_lost_func,  # type: Callable[..., None]
    ):
        # type: (...) -> None
        # Spans from different traces cannot be emitted in the same envelope
        # since the envelope contains a shared trace header. That's why we bucket
        # by trace_id, so that we can then send the buckets each in its own
        # envelope.
        # trace_id -> span buffer
        self._span_buffer = defaultdict(list)  # type: dict[str, list[Span]]
        self._capture_func = capture_func
        self._record_lost_func = record_lost_func
        self._running = True
        self._lock = threading.Lock()

        self._flush_event = threading.Event()  # type: threading.Event

        self._flusher = None  # type: Optional[threading.Thread]
        self._flusher_pid = None  # type: Optional[int]

    def _ensure_thread(self):
        # type: (...) -> bool
        """For forking processes we might need to restart this thread.
        This ensures that our process actually has that thread running.
        """
        if not self._running:
            return False

        pid = os.getpid()
        if self._flusher_pid == pid:
            return True

        with self._lock:
            # Recheck to make sure another thread didn't get here and start the
            # the flusher in the meantime
            if self._flusher_pid == pid:
                return True

            self._flusher_pid = pid

            self._flusher = threading.Thread(target=self._flush_loop)
            self._flusher.daemon = True

            try:
                self._flusher.start()
            except RuntimeError:
                # Unfortunately at this point the interpreter is in a state that no
                # longer allows us to spawn a thread and we have to bail.
                self._running = False
                return False

        return True

    def _flush_loop(self):
        # type: (...) -> None
        while self._running:
            self._flush_event.wait(self.FLUSH_WAIT_TIME + random.random())
            self._flush_event.clear()
            self._flush()

    def get_size(self):
        # type: () -> int
        # caller is responsible for locking before checking this
        return sum(len(buffer) for buffer in self._span_buffer.values())

    def add(self, span):
        # type: (Span) -> None
        if not self._ensure_thread() or self._flusher is None:
            return None

        with self._lock:
            if self.get_size() >= self.MAX_SPANS_BEFORE_DROP:
                self._record_lost_func(
                    reason="queue_overflow",
                    data_category="span",
                    quantity=1,
                )
                return None

            self._span_buffer[span.trace_id].append(span)
            if (
                self.get_size() >= self.MAX_SPANS_BEFORE_FLUSH
            ):  # TODO[span-first] should this be per bucket?
                self._flush_event.set()

    def kill(self):
        # type: (...) -> None
        if self._flusher is None:
            return

        self._running = False
        self._flush_event.set()
        self._flusher = None

    def flush(self):
        # type: (...) -> None
        self._flush()

    @staticmethod
    def _span_to_transport_format(span):
        # type: (Span) -> SpanV2
        from sentry_sdk.utils import attribute_value_to_transport_format, safe_repr

        res = {
            "trace_id": span.trace_id,
            "span_id": span.span_id,
            "name": span.description,  # TODO[span-first]
            "status": SPANSTATUS.OK
            if span.status in (SPANSTATUS.OK, SPANSTATUS.UNSET)
            else SPANSTATUS.ERROR,
            "is_segment": span.containing_transaction == span,
            "start_timestamp": span.start_timestamp.timestamp(),  # TODO[span-first]
            "end_timestamp": span.timestamp.timestamp(),
        }

        if span.parent_span_id:
            res["parent_span_id"] = span.parent_span_id

        if span._attributes:
            res["attributes"] = {
                k: attribute_value_to_transport_format(v)
                for (k, v) in span._attributes.items()
            }

        return res

    def _flush(self):
        # type: (...) -> Optional[Envelope]
        from sentry_sdk.utils import format_timestamp

        with self._lock:
            if len(self._span_buffer) == 0:
                return None

            for trace_id, spans in self._span_buffer.items():
                envelope = Envelope(
                    headers={
                        "sent_at": format_timestamp(datetime.now(timezone.utc)),
                    }
                    # TODO[span-first] more headers
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
                                    self._span_to_transport_format(span)
                                    for span in spans
                                ]
                            }
                        ),
                    )
                )

            self._span_buffer.clear()

        self._capture_func(envelope)
        return envelope
