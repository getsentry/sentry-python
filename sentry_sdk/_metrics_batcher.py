import math
import random
import time
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk._batcher import Batcher
from sentry_sdk.utils import capture_internal_exceptions, logger, serialize_attribute

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, List, Optional, Tuple, Union

    from sentry_sdk._types import Metric, MetricWindowSamplingOptions
    from sentry_sdk.envelope import Envelope

DEFAULT_SAMPLE_PER_SECOND = 2
# Matches the flush interval, so rates down to 1 per flush work
CHUNK_SECONDS = 5.0


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
        sample_early: bool = False,
    ) -> None:
        # Experimental: keep at most this many metrics per name per chunk,
        # picked by reservoir sampling. Names without a limit are not sampled.
        self._default_limit: "Optional[Tuple[int, float]]" = None
        self._name_limits: "Dict[str, Tuple[int, float]]" = {}
        # Only safe without before_send_metric, which must see every metric
        self._sample_early = sample_early
        if window_sampling:
            options = window_sampling if isinstance(window_sampling, dict) else {}
            per_name = options.get("per_name")
            if isinstance(per_name, dict):
                self._name_limits = {
                    name: self._parse_limit(rate) for name, rate in per_name.items()
                }
            if "sample_per_second" in options or not self._name_limits:
                self._default_limit = self._parse_limit(
                    options.get("sample_per_second", DEFAULT_SAMPLE_PER_SECOND)
                )
        self._sampling = bool(self._name_limits) or self._default_limit is not None

        self._reset_sampling_state()
        super().__init__(capture_func, record_lost_func)

    def _reset_thread_state(self) -> None:
        super()._reset_thread_state()
        self._reset_sampling_state()

    def _reset_sampling_state(self) -> None:
        self._chunks: "Dict[str, _Chunk]" = {}
        # Slots held by open chunks. They share MAX_BEFORE_DROP with the buffer,
        # so memory stays bounded however many names there are.
        self._held = 0
        self._sampled_out: "List[Tuple[Metric, int]]" = []

    def _parse_limit(self, value: float) -> "Tuple[int, float]":
        # Below one per chunk, the chunk gets longer and keeps one metric
        rate = _parse_rate(value)
        seconds = max(CHUNK_SECONDS, 1 / rate)
        return min(max(1, int(rate * seconds)), self.MAX_BEFORE_DROP), seconds

    def _limit_for(self, name: str) -> "Optional[Tuple[int, float]]":
        return self._name_limits.get(name, self._default_limit)

    def should_capture(self, name: str) -> bool:
        # Called before the metric is built, so sampled out metrics are cheap
        if not (self._sampling and self._sample_early):
            return True
        self._active.reserved = None
        if getattr(self._active, "flag", False):
            return True

        limit = self._limit_for(name)
        if limit is None:
            return True

        with capture_internal_exceptions():
            in_sampled_span = _in_sampled_span()
            with self._lock:
                chunk = self._get_chunk(name, limit)
                stratum = chunk.sampled if in_sampled_span else chunk.other
                if not self._has_room(chunk, stratum):
                    # Let add() drop and report it
                    return True
                index = self._pick_index(chunk, stratum)
                if index is None:
                    return False
                self._active.reserved = (name, chunk, stratum, index)
        return True

    def _add_locked(self, item: "Metric") -> bool:
        limit = self._limit_for(item["name"]) if self._sampling else None
        if limit is None:
            if len(self._buffer) + self._held >= self.MAX_BEFORE_DROP:
                return False
            return super()._add_locked(item)

        reserved: "Optional[Tuple[str, _Chunk, _Stratum, int]]" = getattr(
            self._active, "reserved", None
        )
        self._active.reserved = None
        with capture_internal_exceptions():
            name = item["name"]
            if reserved is not None and reserved[0] == name:
                _, reserved_chunk, stratum, index = reserved
                # If the chunk closed in between, it already counted this metric
                # as sampled out
                if self._chunks.get(name) is reserved_chunk:
                    stratum.kept[index] = item
                return True

            chunk = self._get_chunk(name, limit)
            stratum = chunk.sampled if _in_sampled_span() else chunk.other
            if not self._has_room(chunk, stratum):
                return False
            index = self._pick_index(chunk, stratum)
            if index is not None:
                stratum.kept[index] = item
        return True

    def _get_chunk(self, name: str, limit: "Tuple[int, float]") -> "_Chunk":
        now = time.monotonic()
        chunk = self._chunks.get(name)
        if chunk is not None and self._is_done(chunk, now):
            self._close(chunk)
            del self._chunks[name]
            chunk = None

        if chunk is None:
            chunk = self._chunks[name] = _Chunk(now, *limit)
        return chunk

    def _has_room(self, chunk: "_Chunk", stratum: "_Stratum") -> bool:
        # A full reservoir replaces slots, so only a new slot needs room
        return (
            len(stratum.kept) >= chunk.size
            or len(self._buffer) + self._held < self.MAX_BEFORE_DROP
        )

    def _pick_index(self, chunk: "_Chunk", stratum: "_Stratum") -> "Optional[int]":
        # Reservoir sampling (Algorithm R). Slots are reserved with None until
        # the metric arrives.
        stratum.seen += 1
        if len(stratum.kept) < chunk.size:
            stratum.kept.append(None)
            self._held += 1
            return len(stratum.kept) - 1
        index = random.randrange(stratum.seen)
        return index if index < chunk.size else None

    def _is_full(self, chunk: "_Chunk") -> bool:
        return len(chunk.sampled.kept) + len(chunk.other.kept) >= chunk.size

    def _is_done(self, chunk: "_Chunk", now: float) -> bool:
        # Chunks that aren't full were not sampled, so they can wait for the flush
        return self._is_full(chunk) and now - chunk.start >= chunk.seconds

    def _close(self, chunk: "_Chunk") -> None:
        sampled, other = chunk.sampled, chunk.other
        self._held -= len(sampled.kept) + len(other.kept)
        # Metrics in sampled spans get the budget first. The others keep at
        # least one slot, so they can still be extrapolated.
        other_floor = 1 if other.kept else 0
        sampled_count = min(len(sampled.kept), chunk.size - other_floor)
        other_count = min(len(other.kept), chunk.size - sampled_count)

        if sampled.kept and not sampled_count:
            # A single slot can't keep both without bias, so pick one uniformly
            # from every metric seen
            seen = sampled.seen + other.seen
            stratum = sampled if random.randrange(seen) < sampled.seen else other
            kept = self._keep(stratum, 1, seen)
        else:
            kept = self._keep(sampled, sampled_count, sampled.seen)
            kept += self._keep(other, other_count, other.seen)

        self._buffer.extend(kept)
        if len(self._buffer) >= self.MAX_BEFORE_FLUSH:
            self._flush_event.set()

    def _keep(self, stratum: "_Stratum", count: int, seen: int) -> "List[Metric]":
        # A random subset of a uniform sample is still a uniform sample
        slots = stratum.kept
        if count < len(slots):
            slots = random.sample(slots, count)
        kept = [item for item in slots if item is not None]

        if kept and seen > len(kept):
            rate = len(kept) / seen
            for item in kept:
                attributes = item["attributes"]
                client_rate = attributes.get("sentry.client_sample_rate")
                if not (isinstance(client_rate, (int, float)) and 0 < client_rate <= 1):
                    client_rate = 1.0
                attributes["sentry.client_sample_rate"] = rate * client_rate
            self._sampled_out.append((kept[0], seen - len(kept)))
        return kept

    def _close_chunks(self, force: bool) -> None:
        now = time.monotonic()
        for name, chunk in list(self._chunks.items()):
            # Full chunks stay open for their length, so they don't go over the rate
            if force or not self._is_full(chunk) or self._is_done(chunk, now):
                self._close(chunk)
                del self._chunks[name]

    def flush(self) -> None:
        if self._sampling:
            with self._lock:
                self._close_chunks(force=True)
        super().flush()

    def _flush(self) -> "Optional[Envelope]":
        if not self._sampling:
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
    __slots__ = ("start", "size", "seconds", "sampled", "other")

    def __init__(self, start: float, size: int, seconds: float) -> None:
        self.start = start
        self.size = size
        self.seconds = seconds
        # Metrics recorded in a sampled span, preferred when picking what to keep
        self.sampled = _Stratum()
        self.other = _Stratum()


class _Stratum:
    __slots__ = ("seen", "kept")

    def __init__(self) -> None:
        self.seen = 0
        self.kept: "List[Optional[Metric]]" = []


def _in_sampled_span() -> bool:
    # _span holds the active span in both trace lifecycles
    span = sentry_sdk.get_current_scope()._span
    return span is not None and span.sampled is True


def _parse_rate(value: float) -> float:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        rate = math.nan

    if math.isfinite(rate) and rate > 0:
        return rate

    logger.warning(
        "[Metrics] Invalid sample rate %r, using %s", value, DEFAULT_SAMPLE_PER_SECOND
    )
    return DEFAULT_SAMPLE_PER_SECOND
