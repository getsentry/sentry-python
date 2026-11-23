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
    """The metrics collected by one thread in a given window."""

    __slots__ = ("reservoirs",)

    def __init__(self) -> None:
        self.reservoirs: "defaultdict[str, _Reservoir]" = defaultdict(_Reservoir)


class _Window:
    """
    One time window in which multiple threads concurrently capture metrics.
    Only the thread with the given identifier should change its local state.
    """

    __slots__ = ("start", "threads", "retire_claims", "number_kept")

    def __init__(self) -> None:
        self.start = time.monotonic()
        self.threads: "defaultdict[int, _ThreadState]" = defaultdict(_ThreadState)
        self.retire_claims: "list[object]" = []
        self.number_kept = 0


class LockFreeMetricsBatcher(Batcher["Metric"]):
    MAX_UNSAMPLED_BEFORE_FLUSH = 1000
    MAX_UNSAMPLED_BEFORE_DROP = 10_000
    MAX_KEPT_PER_WINDOW = 10_000
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

        self._limits = {
            name: int(rate * self.FLUSH_WAIT_TIME)
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
        # Bail out if the current thread is already executing batcher code.
        if getattr(self._active, "flag", False):
            return None

        self._active.flag = True
        try:
            if not self._ensure_thread():
                return None

            window = self._window
            if time.monotonic() - window.start >= self.FLUSH_WAIT_TIME:
                self._retire_window(window)
                window = self._window

            limit = self._limits.get(item["name"])
            if limit is None:
                if len(self._queue) >= self.MAX_UNSAMPLED_BEFORE_DROP:
                    self._record_lost(item)
                    return None

                self._queue.append(item)
                if (
                    len(self._queue) < self.MAX_UNSAMPLED_BEFORE_FLUSH
                    or self._flush_event.is_set()
                ):
                    return

                self._flush_event.set()
                return

            self._add_to_window(item, window, limit)
        finally:
            self._active.flag = False

    def _add_to_window(self, item: "Metric", window: "_Window", limit: int) -> None:
        state = window.threads[threading.get_ident()]
        reservoir = state.reservoirs[item["name"]]
        reservoir.seen += 1
        if (
            len(reservoir.kept) < limit
            and window.number_kept < self.MAX_KEPT_PER_WINDOW
        ):
            window.number_kept += 1
            reservoir.kept.append(item)
            return

        index = random.randrange(reservoir.seen)
        if index >= len(reservoir.kept):
            return

        reservoir.kept[index] = item

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

        now = time.monotonic()
        window = self._window
        if now - window.start >= self.FLUSH_WAIT_TIME:
            self._retire_window(window)

        for window in self._retired.copy():
            if (
                threading.current_thread() is self._flusher
                and now - window.start < self.FLUSH_WAIT_TIME + 0.05
            ):
                break
            try:
                window = self._retired.popleft()
            except IndexError:
                break
            sampled_out += self._harvest(window, items)

        if sampled_out:
            self._record_lost_func(
                reason="sample_rate",
                data_category=self.CLIENT_REPORT_TYPE,
                quantity=sampled_out,
            )

        if not items:
            return None

        envelope = Envelope(
            headers={"sent_at": format_timestamp(datetime.now(timezone.utc))}
        )
        envelope.add_item(self._to_envelope_item(items))
        self._capture_func(envelope)
        return envelope

    def _harvest(self, window: "_Window", items: "list[Metric]") -> int:
        by_name: "dict[str, list[_Reservoir]]" = {}
        for state in window.threads.copy().values():
            for name, reservoir in state.reservoirs.copy().items():
                by_name.setdefault(name, []).append(reservoir)

        sampled_out = 0
        for name, reservoirs in by_name.items():
            limit = self._limits[name]
            bounds = list(
                itertools.accumulate(reservoir.seen for reservoir in reservoirs)
            )
            slots = [0] * len(reservoirs)
            for position in random.sample(range(bounds[-1]), min(limit, bounds[-1])):
                slots[bisect.bisect_right(bounds, position)] += 1

            kept: "list[Metric]" = []
            for reservoir, count in zip(reservoirs, slots):
                thread_kept = reservoir.kept.copy()
                kept.extend(random.sample(thread_kept, min(count, len(thread_kept))))
            if not kept:
                sampled_out += bounds[-1]
                continue

            items.extend(kept)
            rate = len(kept) / bounds[-1]
            for item in kept:
                user_rate = item["attributes"].get("sentry.client_sample_rate")
                if isinstance(user_rate, (int, float)) and not isinstance(
                    user_rate, bool
                ):
                    item["attributes"]["sentry.client_sample_rate"] = rate * user_rate
                    continue

                item["attributes"]["sentry.client_sample_rate"] = rate
            sampled_out += bounds[-1] - len(kept)

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
