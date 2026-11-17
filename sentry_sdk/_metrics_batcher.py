import math
import random
import time
from typing import TYPE_CHECKING

from sentry_sdk._batcher import Batcher
from sentry_sdk.utils import capture_internal_exceptions, logger, serialize_attribute

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, List, Optional, Tuple, Union

    from sentry_sdk._types import Metric, MetricWindowSamplingOptions
    from sentry_sdk.envelope import Envelope

DEFAULT_SAMPLE_PER_SECOND = 10
CHUNK_SECONDS = 1.0


class MetricsBatcher(Batcher["Metric"]):
    MAX_BEFORE_FLUSH = 1000
    MAX_BEFORE_DROP = 10_000
    FLUSH_WAIT_TIME = 5.0

    TYPE = "trace_metric"
    CLIENT_REPORT_TYPE = "trace_metric"
    CONTENT_TYPE = "application/vnd.sentry.items.trace-metric+json"

    def __init__(
        self,
        capture_func: "Callable[[Envelope], None]",
        record_lost_func: "Callable[..., None]",
        window_sampling: "Union[bool, MetricWindowSamplingOptions, None]" = None,
    ) -> None:
        # Experimental: keep at most this many metrics per name per chunk,
        # picked by reservoir sampling.
        self._max_per_chunk = 0
        if window_sampling:
            options = window_sampling if isinstance(window_sampling, dict) else {}
            rate = _parse_rate(
                options.get("sample_per_second", DEFAULT_SAMPLE_PER_SECOND)
            )
            self._max_per_chunk = min(
                max(1, int(rate * CHUNK_SECONDS)), self.MAX_BEFORE_DROP
            )
            # Bounds the open chunks to MAX_BEFORE_DROP metrics
            self._max_names = self.MAX_BEFORE_DROP // self._max_per_chunk

        self._reset_sampling_state()
        super().__init__(capture_func, record_lost_func)

    def _reset_thread_state(self) -> None:
        super()._reset_thread_state()
        self._reset_sampling_state()

    def _reset_sampling_state(self) -> None:
        self._chunks: "Dict[str, _Chunk]" = {}
        self._sampled_out: "List[Tuple[Metric, int]]" = []

    def _add_locked(self, item: "Metric") -> bool:
        if not self._max_per_chunk:
            return super()._add_locked(item)

        name = item["name"]
        now = time.monotonic()
        chunk = self._chunks.get(name)
        if chunk is not None and self._is_done(chunk, now) and self._close(chunk):
            del self._chunks[name]
            chunk = None

        if chunk is None:
            if len(self._chunks) >= self._max_names:
                return False
            chunk = self._chunks[name] = _Chunk(now)

        if not self._is_full(chunk):
            chunk.kept.append(item)
        else:
            j = random.randrange(chunk.seen + 1)
            if j < self._max_per_chunk:
                chunk.kept[j] = item
        chunk.seen += 1
        return True

    def _is_full(self, chunk: "_Chunk") -> bool:
        return len(chunk.kept) >= self._max_per_chunk

    def _is_done(self, chunk: "_Chunk", now: float) -> bool:
        # Chunks that aren't full were not sampled, so they can wait for the flush
        return self._is_full(chunk) and now - chunk.start >= CHUNK_SECONDS

    def _close(self, chunk: "_Chunk") -> bool:
        # If the buffer is full, keep sampling into the open chunk instead
        if len(self._buffer) + len(chunk.kept) > self.MAX_BEFORE_DROP:
            return False

        if chunk.seen > len(chunk.kept):
            rate = len(chunk.kept) / chunk.seen
            for item in chunk.kept:
                attributes = item["attributes"]
                attributes["sentry.client_sample_rate"] = rate * _parse_rate(
                    attributes.get("sentry.client_sample_rate", 1.0), default=1.0
                )
            self._sampled_out.append((chunk.kept[0], chunk.seen - len(chunk.kept)))

        self._buffer.extend(chunk.kept)
        if len(self._buffer) >= self.MAX_BEFORE_FLUSH:
            self._flush_event.set()
        return True

    def _close_chunks(self, force: bool) -> None:
        now = time.monotonic()
        for name, chunk in list(self._chunks.items()):
            # Full chunks stay open for CHUNK_SECONDS, so they don't go over the rate
            if force or not self._is_full(chunk) or self._is_done(chunk, now):
                if self._close(chunk):
                    del self._chunks[name]

    def flush(self) -> None:
        if self._max_per_chunk:
            with self._lock:
                self._close_chunks(force=True)
        super().flush()

    def _flush(self) -> "Optional[Envelope]":
        if not self._max_per_chunk:
            return super()._flush()

        with self._lock:
            self._close_chunks(force=False)
        envelope = super()._flush()

        # Record outside of the lock, since this serializes metrics
        with self._lock:
            sampled_out, self._sampled_out = self._sampled_out, []

        for item, count in sampled_out:
            self._record_lost_func(
                reason="sample_rate",
                data_category=self.CLIENT_REPORT_TYPE,
                quantity=count,
            )
            # Estimate the lost bytes from one metric of that name
            with capture_internal_exceptions():
                self._record_lost_func(
                    reason="sample_rate",
                    data_category="trace_metric_byte",
                    quantity=count * len(self._to_envelope_item([item]).get_bytes()),
                )

        return envelope

    @staticmethod
    def _to_transport_format(item: "Metric") -> "Any":
        res = {
            "timestamp": item["timestamp"],
            "name": item["name"],
            "type": item["type"],
            "value": item["value"],
            "attributes": {
                k: serialize_attribute(v) for (k, v) in item["attributes"].items()
            },
        }

        if item.get("trace_id") is not None:
            res["trace_id"] = item["trace_id"]

        if item.get("span_id") is not None:
            res["span_id"] = item["span_id"]

        if item.get("unit") is not None:
            res["unit"] = item["unit"]

        return res


class _Chunk:
    __slots__ = ("start", "seen", "kept")

    def __init__(self, start: float) -> None:
        self.start = start
        self.seen = 0
        self.kept: "List[Metric]" = []


def _parse_rate(value: "Any", default: float = DEFAULT_SAMPLE_PER_SECOND) -> float:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        rate = math.nan

    if math.isfinite(rate) and rate > 0:
        return rate

    logger.warning("[Metrics] Invalid sample rate %r, using %s", value, default)
    return default
