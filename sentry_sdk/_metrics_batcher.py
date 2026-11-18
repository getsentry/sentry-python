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

DEFAULT_SAMPLE_PER_SECOND = 2
# Matches FLUSH_WAIT_TIME, so each window goes out in one flush
WINDOW_SECONDS = 5.0


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
        self._default_limit: "Optional[int]" = None
        self._name_limits: "Dict[str, int]" = {}
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
        # before_send_metric has to see every metric, so we can't sample before
        # it runs
        self._sample_early = self._sampling and sample_early

        self._reset_sampling_state()
        super().__init__(capture_func, record_lost_func)

    def _reset_thread_state(self) -> None:
        super()._reset_thread_state()
        self._reset_sampling_state()

    def _reset_sampling_state(self) -> None:
        self._window_start = time.monotonic()
        self._reservoirs: "Dict[str, List[Optional[Metric]]]" = {}
        self._seen: "Dict[str, int]" = {}
        # Reservoirs count against MAX_BEFORE_DROP together with the buffer, so
        # memory stays bounded no matter how many names there are
        self._held = 0
        self._sampled_out: "List[Tuple[Metric, int]]" = []

    def _parse_limit(self, value: float) -> int:
        try:
            rate = float(value)
        except (TypeError, ValueError):
            rate = math.nan

        if not (math.isfinite(rate) and rate > 0):
            logger.warning(
                "[Metrics] Given window sampling rate is invalid. It must be a positive number. Got %r, using %s instead.",
                value,
                DEFAULT_SAMPLE_PER_SECOND,
            )
            rate = DEFAULT_SAMPLE_PER_SECOND

        # Never go below 1, so we always keep at least one metric per name to
        # extrapolate from
        return min(max(1, int(rate * WINDOW_SECONDS)), self.MAX_BEFORE_DROP)

    def should_capture(self, name: str) -> bool:
        # This runs before the metric is built, so sampled out metrics stay cheap
        if not self._sample_early:
            return True
        self._active.reserved = None
        if getattr(self._active, "flag", False):
            return True

        limit = self._name_limits.get(name, self._default_limit)
        if limit is None:
            return True

        with capture_internal_exceptions(), self._lock:
            sample = self._sample(name, limit)
            if sample is None:
                # Let add() drop the metric, since that's where we record
                # queue_overflow
                return True
            reservoir, index = sample
            if index is None:
                return False
            self._active.reserved = (name, reservoir, index)
        return True

    def _add_locked(self, item: "Metric") -> bool:
        if not self._sampling:
            return super()._add_locked(item)

        reserved: "Optional[Tuple[str, List[Optional[Metric]], int]]" = getattr(
            self._active, "reserved", None
        )
        self._active.reserved = None

        name = item["name"]
        limit = self._name_limits.get(name, self._default_limit)
        if limit is None:
            if len(self._buffer) + self._held >= self.MAX_BEFORE_DROP:
                return False
            return super()._add_locked(item)

        if reserved is not None and reserved[0] == name:
            _, reservoir, index = reserved
            # If the window closed in the meantime, it already counted this
            # metric as sampled out
            if self._reservoirs.get(name) is reservoir:
                reservoir[index] = item
            return True

        sample = self._sample(name, limit)
        if sample is None:
            return False
        reservoir, index = sample
        if index is not None:
            reservoir[index] = item
        return True

    def _sample(
        self, name: str, limit: int
    ) -> "Optional[Tuple[List[Optional[Metric]], Optional[int]]]":
        if time.monotonic() - self._window_start >= WINDOW_SECONDS:
            self._close_window()

        reservoir = self._reservoirs.setdefault(name, [])
        seen = self._seen.get(name, 0) + 1
        if len(reservoir) >= limit:
            self._seen[name] = seen
            index = random.randrange(seen)
            return reservoir, index if index < limit else None

        # Replacing a slot in a full reservoir doesn't need more memory, only a
        # new slot does
        if len(self._buffer) + self._held >= self.MAX_BEFORE_DROP:
            return None
        self._seen[name] = seen
        self._held += 1
        # We hold the slot with None so that should_capture can decide before
        # add() has the metric
        reservoir.append(None)
        return reservoir, len(reservoir) - 1

    def _close_window(self) -> None:
        for name, reservoir in self._reservoirs.items():
            kept = [item for item in reservoir if item is not None]
            seen = self._seen.get(name, 0)
            if kept and seen > len(kept):
                rate = len(kept) / seen
                for item in kept:
                    attributes = item["attributes"]
                    client_rate = attributes.get("sentry.client_sample_rate")
                    if isinstance(client_rate, (int, float)) and 0 < client_rate <= 1:
                        attributes["sentry.client_sample_rate"] = rate * client_rate
                    else:
                        attributes["sentry.client_sample_rate"] = rate
                self._sampled_out.append((kept[0], seen - len(kept)))
            self._buffer.extend(kept)

        self._reservoirs = {}
        self._seen = {}
        self._held = 0
        self._window_start = time.monotonic()
        if len(self._buffer) >= self.MAX_BEFORE_FLUSH:
            self._flush_event.set()

    def flush(self) -> None:
        if self._sampling:
            with self._lock:
                self._close_window()
        super().flush()

    def _flush(self) -> "Optional[Envelope]":
        if not self._sampling:
            return super()._flush()

        with self._lock:
            # Early flushes leave the window open, so a name can't go over its
            # rate
            if time.monotonic() - self._window_start >= WINDOW_SECONDS:
                self._close_window()
            sampled_out, self._sampled_out = self._sampled_out, []

        envelope = super()._flush()

        # Record outside of the lock, since this serializes a metric and every
        # add() would wait on it
        for item, count in sampled_out:
            self._record_lost_func(
                reason="sample_rate",
                data_category=self.CLIENT_REPORT_TYPE,
                quantity=count,
            )
            # Serializing every sampled out metric would cost as much as we saved
            # by sampling, so we estimate from one
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
