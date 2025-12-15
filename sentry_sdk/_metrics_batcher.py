import os
import random
import threading
from datetime import datetime, timezone
from typing import Optional, List, Callable, TYPE_CHECKING, Any, Union

from sentry_sdk.utils import format_timestamp, safe_repr
from sentry_sdk.envelope import Envelope, Item, PayloadRef

if TYPE_CHECKING:
    from sentry_sdk._types import Metric


class MetricsBatcher:
    MAX_METRICS_BEFORE_FLUSH = 1000
    MAX_METRICS_BEFORE_DROP = 10_000
    FLUSH_WAIT_TIME = 5.0

    def __init__(
        self,
        capture_func: "Callable[[Envelope], None]",
        record_lost_func: "Callable[..., None]",
    ) -> None:
        self._metric_buffer: "List[Metric]" = []
        self._capture_func = capture_func
        self._record_lost_func = record_lost_func
        self._running = True
        self._lock = threading.Lock()

        self._flush_event: "threading.Event" = threading.Event()

        self._flusher: "Optional[threading.Thread]" = None
        self._flusher_pid: "Optional[int]" = None

    def _ensure_thread(self) -> bool:
        if not self._running:
            return False

        pid = os.getpid()
        if self._flusher_pid == pid:
            return True

        with self._lock:
            if self._flusher_pid == pid:
                return True

            self._flusher_pid = pid

            self._flusher = threading.Thread(target=self._flush_loop)
            self._flusher.daemon = True

            try:
                self._flusher.start()
            except RuntimeError:
                self._running = False
                return False

        return True

    def _flush_loop(self) -> None:
        while self._running:
            self._flush_event.wait(self.FLUSH_WAIT_TIME + random.random())
            self._flush_event.clear()
            self._flush()

    def add(
        self,
        metric: "Metric",
    ) -> None:
        if not self._ensure_thread() or self._flusher is None:
            return None

        with self._lock:
            if len(self._metric_buffer) >= self.MAX_METRICS_BEFORE_DROP:
                self._record_lost_func(
                    reason="queue_overflow",
                    data_category="trace_metric",
                    quantity=1,
                )
                return None

            self._metric_buffer.append(metric)
            if len(self._metric_buffer) >= self.MAX_METRICS_BEFORE_FLUSH:
                self._flush_event.set()

    def kill(self) -> None:
        if self._flusher is None:
            return

        self._running = False
        self._flush_event.set()
        self._flusher = None

    def flush(self) -> None:
        self._flush()

    @staticmethod
    def _metric_to_transport_format(metric: "Metric") -> "Any":
        def format_attribute(val: "Union[int, float, str, bool]") -> "Any":
            if isinstance(val, bool):
                return {"value": val, "type": "boolean"}
            if isinstance(val, int):
                return {"value": val, "type": "integer"}
            if isinstance(val, float):
                return {"value": val, "type": "double"}
            if isinstance(val, str):
                return {"value": val, "type": "string"}
            return {"value": safe_repr(val), "type": "string"}

        res = {
            "timestamp": metric["timestamp"],
            "trace_id": metric["trace_id"],
            "name": metric["name"],
            "type": metric["type"],
            "value": metric["value"],
            "attributes": {
                k: format_attribute(v) for (k, v) in metric["attributes"].items()
            },
        }

        if metric.get("span_id") is not None:
            res["span_id"] = metric["span_id"]

        if metric.get("unit") is not None:
            res["unit"] = metric["unit"]

        return res

    def _flush(self) -> "Optional[Envelope]":
        envelope = Envelope(
            headers={"sent_at": format_timestamp(datetime.now(timezone.utc))}
        )
        with self._lock:
            if len(self._metric_buffer) == 0:
                return None

            envelope.add_item(
                Item(
                    type="trace_metric",
                    content_type="application/vnd.sentry.items.trace-metric+json",
                    headers={
                        "item_count": len(self._metric_buffer),
                    },
                    payload=PayloadRef(
                        json={
                            "items": [
                                self._metric_to_transport_format(metric)
                                for metric in self._metric_buffer
                            ]
                        }
                    ),
                )
            )
            self._metric_buffer.clear()

        self._capture_func(envelope)
        return envelope
