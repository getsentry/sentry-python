import random
from typing import TYPE_CHECKING

from sentry_sdk._batcher import Batcher
from sentry_sdk.utils import capture_internal_exceptions, serialize_attribute

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, List, Optional, Tuple, Union

    from sentry_sdk._types import Attributes, Metric, MetricWindowSamplingOptions
    from sentry_sdk.envelope import Envelope

# Experimental window sampling, see `MetricsBatcher`.
DEFAULT_SAMPLE_PER_SECOND = 10
# Lowest factor that back pressure can scale every allowance down to.
MIN_PRESSURE = 0.01

# Private key set by `sentry_sdk.metrics` when the caller passes
# `sample_per_second`. It is never serialized (see `_to_transport_format`).
SAMPLE_PER_SECOND_KEY = "_sample_per_second"
CLIENT_SAMPLE_RATE_ATTRIBUTE = "sentry.client_sample_rate"


class _NameWindow:
    """Per metric name state for the current flush window."""

    __slots__ = ("base_allowance", "allowance", "seen", "indices")

    def __init__(self, base_allowance: int, allowance: int) -> None:
        # What this name asked for: `sample_per_second * FLUSH_WAIT_TIME`.
        self.base_allowance = base_allowance
        # What it gets this window, after back pressure.
        self.allowance = allowance
        self.seen = 0
        # Positions of this name's metrics in the shared buffer.
        self.indices: "List[int]" = []


class MetricsBatcher(Batcher["Metric"]):
    """
    With the experimental `metrics_window_sampling` option, the flush window
    (`FLUSH_WAIT_TIME`, ~5s) is also the sampling window. Each metric name
    keeps at most `sample_per_second * FLUSH_WAIT_TIME` metrics (50 at the
    default 10/s) in the regular buffer per window, picked by reservoir
    sampling, so every metric seen in the window has the same chance to be
    kept. At flush, kept metrics get `sentry.client_sample_rate = kept / seen`
    (multiplied into any rate the caller set) so counts extrapolate.

    Back pressure: when the buffer reaches `MAX_BEFORE_FLUSH` and flushes
    early, every name's allowance is halved for the next window. Each window
    that flushes on the timer doubles it back, up to what was asked for.
    Metrics dropped because of a lowered allowance are reported as
    `backpressure`, the rest as `sample_rate`.

    The buffer itself is unchanged: metrics are appended or replaced in place,
    and the per-name state is cleared with the buffer on every flush.
    """

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
        self._sample_per_second: "Optional[float]" = None
        if window_sampling:
            options = {} if window_sampling is True else window_sampling
            self._sample_per_second = options.get(
                "sample_per_second", DEFAULT_SAMPLE_PER_SECOND
            )

        self._reset_window_state()
        super().__init__(capture_func, record_lost_func)

    def _reset_thread_state(self) -> None:
        super()._reset_thread_state()
        self._reset_window_state()

    def _reset_window_state(self) -> None:
        self._windows: "Dict[str, _NameWindow]" = {}
        self._pressure = 1.0
        self._hit_flush_cap = False
        # (sample item, sample_rate drops, backpressure drops) per name,
        # recorded after the flush releases the lock.
        self._pending_lost: "List[Tuple[Metric, int, int]]" = []

    def _add_locked(self, item: "Metric") -> bool:
        if self._sample_per_second is None:
            return super()._add_locked(item)

        name = item["name"]
        window = self._windows.get(name)
        if window is None:
            per_second = self._sample_per_second
            try:
                per_second = float(item.get(SAMPLE_PER_SECOND_KEY, per_second))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                pass
            base_allowance = max(1, int(per_second * self.FLUSH_WAIT_TIME))
            window = _NameWindow(
                base_allowance, max(1, int(base_allowance * self._pressure))
            )

        if len(window.indices) < window.allowance:
            if not super()._add_locked(item):
                return False
            window.indices.append(len(self._buffer) - 1)
            self._windows[name] = window
            if len(self._buffer) >= self.MAX_BEFORE_FLUSH:
                self._hit_flush_cap = True
        else:
            # Algorithm R: replace a kept metric in place, so every metric
            # seen in the window has the same allowance / seen chance.
            j = random.randrange(window.seen + 1)
            if j < window.allowance:
                self._buffer[window.indices[j]] = item

        window.seen += 1
        return True

    def _add_to_envelope(self, envelope: "Envelope") -> None:
        # Called by `_flush` with the lock held, right before the buffer is
        # cleared.
        if self._sample_per_second is not None:
            self._close_window()
        super()._add_to_envelope(envelope)

    def _close_window(self) -> None:
        for window in self._windows.values():
            kept = len(window.indices)
            if window.seen <= kept:
                continue

            rate = kept / window.seen
            for index in window.indices:
                attributes = self._buffer[index]["attributes"]
                attributes[CLIENT_SAMPLE_RATE_ATTRIBUTE] = rate * _existing_rate(
                    attributes
                )

            wanted = min(window.seen, window.base_allowance)
            self._pending_lost.append(
                (self._buffer[window.indices[0]], window.seen - wanted, wanted - kept)
            )

        if self._hit_flush_cap:
            self._pressure = max(self._pressure / 2, MIN_PRESSURE)
        else:
            self._pressure = min(self._pressure * 2, 1.0)

        self._hit_flush_cap = False
        self._windows = {}

    def _flush(self) -> "Optional[Envelope]":
        envelope = super()._flush()

        with self._lock:
            pending, self._pending_lost = self._pending_lost, []

        for item, sampled, pressured in pending:
            # One serialized metric is the byte estimate for every dropped
            # metric of that name.
            size = 0
            with capture_internal_exceptions():
                size = len(self._to_envelope_item([item]).get_bytes())

            for reason, count in (
                ("sample_rate", sampled),
                ("backpressure", pressured),
            ):
                if count <= 0:
                    continue
                self._record_lost_func(
                    reason=reason, data_category=self.CLIENT_REPORT_TYPE, quantity=count
                )
                if size:
                    self._record_lost_func(
                        reason=reason,
                        data_category="trace_metric_byte",
                        quantity=count * size,
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


def _existing_rate(attributes: "Attributes") -> float:
    try:
        return float(attributes.get(CLIENT_SAMPLE_RATE_ATTRIBUTE, 1.0))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1.0
