import math
import random
from typing import TYPE_CHECKING

from sentry_sdk._batcher import Batcher
from sentry_sdk.utils import capture_internal_exceptions, logger, serialize_attribute

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, List, Optional, Tuple, Union

    from sentry_sdk._types import Metric, MetricWindowSamplingOptions
    from sentry_sdk.envelope import Envelope

DEFAULT_SAMPLE_PER_SECOND = 10


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
        # Experimental: keep at most this many metrics per name per flush,
        # picked by reservoir sampling.
        self._max_per_name: "Optional[int]" = None
        if window_sampling:
            options = window_sampling if isinstance(window_sampling, dict) else {}
            rate = _parse_rate(
                options.get("sample_per_second", DEFAULT_SAMPLE_PER_SECOND)
            )
            self._max_per_name = min(
                max(1, int(rate * self.FLUSH_WAIT_TIME)), self.MAX_BEFORE_DROP
            )
            # Admitted names always fit their samples, so the buffer can't fill
            # up under a name that is still sampling.
            self._max_names = self.MAX_BEFORE_DROP // self._max_per_name

        self._reset_sampling_state()
        super().__init__(capture_func, record_lost_func)

    def _reset_thread_state(self) -> None:
        super()._reset_thread_state()
        self._reset_sampling_state()

    def _reset_sampling_state(self) -> None:
        self._seen: "Dict[str, int]" = {}
        # metric name -> buffer indices of the kept metrics
        self._kept: "Dict[str, List[int]]" = {}
        self._sampled_out: "List[Tuple[Metric, int]]" = []

    def _add_locked(self, item: "Metric") -> bool:
        if self._max_per_name is None:
            return super()._add_locked(item)

        name = item["name"]
        kept = self._kept.get(name)
        if kept is None:
            if len(self._kept) >= self._max_names:
                return False
            kept = self._kept[name] = []

        seen = self._seen.get(name, 0)
        if len(kept) < self._max_per_name:
            if not super()._add_locked(item):
                return False
            kept.append(len(self._buffer) - 1)
        else:
            j = random.randrange(seen + 1)
            if j < self._max_per_name:
                self._buffer[kept[j]] = item

        self._seen[name] = seen + 1
        return True

    def _add_to_envelope(self, envelope: "Envelope") -> None:
        for name, kept in self._kept.items():
            seen = self._seen[name]
            if seen <= len(kept):
                continue

            rate = len(kept) / seen
            for index in kept:
                attributes = self._buffer[index]["attributes"]
                attributes["sentry.client_sample_rate"] = rate * _parse_rate(
                    attributes.get("sentry.client_sample_rate", 1.0), default=1.0
                )
            self._sampled_out.append((self._buffer[kept[0]], seen - len(kept)))

        self._seen = {}
        self._kept = {}
        super()._add_to_envelope(envelope)

    def _flush(self) -> "Optional[Envelope]":
        envelope = super()._flush()
        if self._max_per_name is None:
            return envelope

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


def _parse_rate(value: "Any", default: float = DEFAULT_SAMPLE_PER_SECOND) -> float:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        rate = math.nan

    if math.isfinite(rate) and rate > 0:
        return rate

    logger.warning("[Metrics] Invalid sample rate %r, using %s", value, default)
    return default
