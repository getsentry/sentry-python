import bisect
import itertools
import os
import random
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sentry_sdk._batcher import Batcher
from sentry_sdk.envelope import Envelope
from sentry_sdk.utils import (
    capture_internal_exceptions,
    format_timestamp,
    serialize_attribute,
)

if TYPE_CHECKING:
    from typing import Any, Callable, Optional

    from sentry_sdk._types import Metric


class _Reservoir:
    __slots__ = ("kept", "seen")

    def __init__(self) -> None:
        self.kept: "list[Metric]" = []
        self.seen = 0


class _ThreadState:
    """The metrics collected by one thread in a given window and their count."""

    __slots__ = ("reservoirs", "size", "admitted")

    def __init__(self) -> None:
        # Keyed by metric name and whether the metric has a span_id
        self.reservoirs: "defaultdict[tuple[str, bool], _Reservoir]" = defaultdict(
            _Reservoir
        )
        self.size = 0
        # Reservoir that should_capture() admitted the metric this thread is
        # capturing to, so add() puts it where it was counted
        self.admitted: "Optional[_Reservoir]" = None


class _Window:
    """
    One time window in which multiple threads concurrently capture metrics.
    Only the thread with the given identifier should change its local state.
    """

    __slots__ = ("start", "threads", "retire_claims")

    def __init__(self) -> None:
        self.start = time.monotonic()
        self.threads: "defaultdict[int, _ThreadState]" = defaultdict(_ThreadState)
        self.retire_claims: "list[object]" = []

    def size(self) -> int:
        return sum(state.size for state in self.threads.copy().values())


class LockFreeMetricsBatcher(Batcher["Metric"]):
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
        window_sampling: "dict[str, float]",
    ) -> None:
        super().__init__(capture_func, record_lost_func)

        # Metric name -> metrics kept per window. Never below 1, so there's
        # always at least one metric per name to extrapolate from
        self._limits = {
            name: min(max(1, int(rate * self.FLUSH_WAIT_TIME)), self.MAX_BEFORE_DROP)
            for name, rate in window_sampling.items()
        }

        self._queue: "deque[Metric]" = deque()
        self._window = _Window()
        self._retired: "deque[_Window]" = deque()
        # Holds the claim of the one thread that gets to start the flusher.
        self._flusher_claim: "dict[str, object]" = {}

    def _reset_thread_state(self) -> None:
        super()._reset_thread_state()
        self._queue = deque()
        self._window = _Window()
        self._retired = deque()
        self._flusher_claim = {}

    def _ensure_thread(self) -> bool:
        """For forking processes we might need to restart this thread.
        This ensures that our process actually has that thread running.
        """
        if not self._running:
            return False

        pid = os.getpid()
        if self._flusher_pid == pid:
            return True

        # dict.setdefault is atomic, so exactly one thread gets to start the flusher.
        claim = object()
        if self._flusher_claim.setdefault("flusher", claim) is not claim:
            return True

        flusher = threading.Thread(target=self._flush_loop)
        flusher.daemon = True
        try:
            flusher.start()
        except RuntimeError:
            # Unfortunately at this point the interpreter is in a state that no
            # longer allows us to spawn a thread and we have to bail.
            self._running = False
            return False

        self._flusher = flusher
        self._flusher_pid = pid
        return True

    def add(self, item: "Metric") -> None:
        if not self._ensure_thread():
            return None

        window = self._window
        if time.monotonic() - window.start >= self.FLUSH_WAIT_TIME:
            self._retire_window(window)
            window = self._window

        if len(self._queue) + window.size() >= self.MAX_BEFORE_DROP:
            self._record_lost(item)
            return None

        limit = self._limits.get(item["name"])
        if limit is None:
            self._queue.append(item)
            if len(self._queue) < self.MAX_BEFORE_FLUSH or self._flush_event.is_set():
                return

            self._flush_event.set()
            return

        state = window.threads[threading.get_ident()]
        reservoir, state.admitted = state.admitted, None
        # Not admitted by should_capture(), e.g. because the window turned
        # over in between
        if reservoir is None:
            reservoir = self._admit(
                state, item["name"], item.get("span_id") is not None, limit
            )
            if reservoir is None:
                return

        state.size += 1
        reservoir.kept.append(item)

    def should_capture(self, name: str, has_span: bool) -> bool:
        """
        Decide whether a metric is kept before it's built, so metrics that
        window sampling drops cost as little as possible.
        """
        limit = self._limits.get(name)
        if limit is None:
            return True

        window = self._window
        if time.monotonic() - window.start >= self.FLUSH_WAIT_TIME:
            self._retire_window(window)
            window = self._window

        state = window.threads[threading.get_ident()]
        state.admitted = self._admit(state, name, has_span, limit)
        return state.admitted is not None

    def _admit(
        self, state: "_ThreadState", name: str, has_span: bool, limit: int
    ) -> "Optional[_Reservoir]":
        # With one slot there's nothing to split, so everything shares a reservoir
        reservoir = state.reservoirs[(name, limit > 1 and has_span)]
        reservoir.seen += 1
        if reservoir.seen <= limit:
            return reservoir

        index = random.randrange(reservoir.seen)
        if index >= limit:
            return None

        # Free the slot now. If the metric doesn't reach add(), e.g. because
        # before_send_metric dropped it, the slot stays empty
        if index < len(reservoir.kept):
            reservoir.kept[index] = reservoir.kept[-1]
            reservoir.kept.pop()
            state.size -= 1
        return reservoir

    def _retire_window(self, window: "_Window") -> None:
        claim = object()
        window.retire_claims.append(claim)
        if window.retire_claims[0] is not claim:
            return

        self._window = _Window()
        self._retired.append(window)

    def flush(self) -> None:
        self._retire_window(self._window)
        super().flush()

    def _flush(self) -> "Optional[Envelope]":
        queue = self._queue
        items: "list[Metric]" = []
        # Only take what's queued now. Producers can keep enqueuing (no locks).
        for _ in range(len(queue)):
            try:
                items.append(queue.popleft())
            except IndexError:
                break

        sampled_out = 0

        window = self._window
        if time.monotonic() - window.start >= self.FLUSH_WAIT_TIME:
            self._retire_window(window)
        for _ in range(len(self._retired)):
            try:
                window = self._retired.popleft()
            except IndexError:
                break
            sampled_out += self._harvest(window, items)

        if not items:
            return None

        envelope = Envelope(
            headers={"sent_at": format_timestamp(datetime.now(timezone.utc))}
        )
        with capture_internal_exceptions():
            envelope.add_item(self._to_envelope_item(items))
            self._capture_func(envelope)

        if sampled_out:
            self._record_lost_func(
                reason="sample_rate",
                data_category=self.CLIENT_REPORT_TYPE,
                quantity=sampled_out,
            )

        return envelope

    def _harvest(self, window: "_Window", items: "list[Metric]") -> int:
        """Uniformly gather metrics from the window and append to `items`.

        Metrics with a span_id and other metrics are sampled separately.
        """
        by_name: "dict[str, dict[bool, list[_Reservoir]]]" = {}
        for state in window.threads.copy().values():
            for (name, in_span), reservoir in state.reservoirs.copy().items():
                by_name.setdefault(name, {}).setdefault(in_span, []).append(reservoir)

        sampled_out = 0
        for name, groups in by_name.items():
            limit = self._limits.get(name)
            if limit is None:
                continue

            in_span = groups.get(True, [])
            other = groups.get(False, [])
            in_span_bounds = list(
                itertools.accumulate(reservoir.seen for reservoir in in_span)
            )
            other_bounds = list(
                itertools.accumulate(reservoir.seen for reservoir in other)
            )
            in_span_seen = in_span_bounds[-1] if in_span_bounds else 0
            other_seen = other_bounds[-1] if other_bounds else 0

            # Keep at least one metric with a span_id in each window so kept
            # metrics link to traces, and split the rest by volume. Each group
            # is sampled with its own rate, so both extrapolate correctly
            if in_span_seen and other_seen:
                in_span_keep = min(
                    in_span_seen,
                    limit - 1,
                    max(1, round(limit * in_span_seen / (in_span_seen + other_seen))),
                )
                other_keep = min(other_seen, limit - in_span_keep)
                in_span_keep = min(in_span_seen, limit - other_keep)
            else:
                in_span_keep = min(in_span_seen, limit)
                other_keep = min(other_seen, limit)

            for reservoirs, bounds, keep in (
                (in_span, in_span_bounds, in_span_keep),
                (other, other_bounds, other_keep),
            ):
                if not keep:
                    continue

                slots = [0] * len(reservoirs)
                for position in random.sample(range(bounds[-1]), keep):
                    slots[bisect.bisect_right(bounds, position)] += 1

                # Slots without a metric were dropped after being sampled,
                # e.g. by before_send_metric, and still count towards the rate
                kept: "list[Metric]" = []
                for reservoir, count in zip(reservoirs, slots):
                    thread_kept = reservoir.kept.copy()
                    for index in random.sample(
                        range(min(limit, reservoir.seen)), count
                    ):
                        if index < len(thread_kept):
                            kept.append(thread_kept[index])

                items.extend(kept)
                if bounds[-1] <= keep:
                    continue

                sampled_out += bounds[-1] - keep
                rate = keep / bounds[-1]
                for item in kept:
                    attributes = item["attributes"]
                    # Callers may have sampled already, so combine with their
                    # rate. Relay ignores rates outside (0, 1], and so do we
                    client_rate = attributes.get("sentry.client_sample_rate")
                    if not (
                        isinstance(client_rate, (int, float)) and 0 < client_rate <= 1
                    ):
                        client_rate = 1.0
                    attributes["sentry.client_sample_rate"] = rate * client_rate

        return sampled_out

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
