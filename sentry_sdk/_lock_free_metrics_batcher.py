import bisect
import itertools
import os
import random
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sentry_sdk.envelope import Envelope, Item, PayloadRef
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

    __slots__ = ("reservoirs", "size")

    def __init__(self) -> None:
        self.reservoirs: "defaultdict[str, _Reservoir]" = defaultdict(_Reservoir)
        self.size = 0


class _Window:
    """
    One time window in which multiple threads concurrently capture metrics.
    Only the thread with the given identifier should change it's local state.
    """

    __slots__ = ("start", "threads", "retire_claims")

    def __init__(self) -> None:
        self.start = time.monotonic()
        self.threads: "defaultdict[int, _ThreadState]" = defaultdict(_ThreadState)
        self.retire_claims: "list[object]" = []

    def size(self) -> int:
        return sum(state.size for state in self.threads.copy().values())


class LockFreeMetricsBatcher:
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
        self._capture_func = capture_func
        self._record_lost_func = record_lost_func

        # Metric name -> metrics kept per window. Never below 1, so there's
        # always at least one metric per name to extrapolate from
        self._limits = {
            name: min(max(1, int(rate * self.FLUSH_WAIT_TIME)), self.MAX_BEFORE_DROP)
            for name, rate in window_sampling.items()
        }

        self._reset_thread_state()

    def _reset_thread_state(self) -> None:
        self._queue: "deque[Metric]" = deque()
        self._window = _Window()
        self._retired: "deque[_Window]" = deque()
        self._running = True
        self._flush_event: "threading.Event" = threading.Event()
        self._flusher: "Optional[threading.Thread]" = None
        self._flusher_pid: "Optional[int]" = None
        # Holds the claim of the one thread that gets to start the flusher.
        self._flusher_claim: "dict[str, object]" = {}

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

    def _flush_loop(self) -> None:
        while self._running:
            self._flush_event.wait(self.FLUSH_WAIT_TIME + random.random())
            self._flush_event.clear()
            self._flush()

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
            self._add_to_queue(item)
        else:
            self._add_to_window(item, window, limit)

    def _add_to_queue(self, item: "Metric") -> None:
        self._queue.append(item)
        if len(self._queue) < self.MAX_BEFORE_FLUSH or self._flush_event.is_set():
            return

        self._flush_event.set()

    def _add_to_window(self, item: "Metric", window: "_Window", limit: int) -> None:
        state = window.threads[threading.get_ident()]
        reservoir = state.reservoirs[item["name"]]
        reservoir.seen += 1
        if len(reservoir.kept) < limit:
            state.size += 1
            reservoir.kept.append(item)
            return

        index = random.randrange(reservoir.seen)
        if index >= limit:
            return

        reservoir.kept[index] = item

    def _retire_window(self, window: "_Window") -> None:
        claim = object()
        window.retire_claims.append(claim)
        if window.retire_claims[0] is not claim:
            return

        self._window = _Window()
        self._retired.append(window)

    def kill(self) -> None:
        if self._flusher is None:
            return

        self._running = False
        self._flush_event.set()
        self._flusher = None

    def flush(self) -> None:
        self._flush(force=True)

    def _flush(self, force: bool = False) -> "Optional[Envelope]":
        queue = self._queue
        items: "list[Metric]" = []
        # Only take what's queued now. Producers can keep enqueuing (no locks).
        for _ in range(len(queue)):
            try:
                items.append(queue.popleft())
            except IndexError:
                break

        sampled_out: "list[tuple[Metric, int]]" = []

        window = self._window
        if force or time.monotonic() - window.start >= self.FLUSH_WAIT_TIME:
            self._retire_window(window)
        for _ in range(len(self._retired)):
            try:
                window = self._retired.popleft()
            except IndexError:
                break
            sampled_out.extend(self._harvest(window, items))

        if not items:
            return None

        envelope = Envelope(
            headers={"sent_at": format_timestamp(datetime.now(timezone.utc))}
        )
        with capture_internal_exceptions():
            envelope.add_item(self._to_envelope_item(items))
            self._capture_func(envelope)

        for item, count in sampled_out:
            self._record_lost_func(
                reason="sample_rate",
                data_category=self.CLIENT_REPORT_TYPE,
                quantity=count,
            )

        return envelope

    def _harvest(
        self, window: "_Window", items: "list[Metric]"
    ) -> "list[tuple[Metric, int]]":
        by_name: "dict[str, list[_Reservoir]]" = {}
        for state in window.threads.copy().values():
            for name, reservoir in state.reservoirs.copy().items():
                by_name.setdefault(name, []).append(reservoir)

        sampled_out = []
        for name, reservoirs in by_name.items():
            limit = self._limits.get(name)
            if limit is None:
                continue

            seen = sum(reservoir.seen for reservoir in reservoirs)
            kept = self._merge(reservoirs, limit, seen)
            if not kept:
                continue

            items.extend(kept)
            if seen <= len(kept):
                continue

            rate = len(kept) / seen
            for item in kept:
                item["attributes"]["sentry.client_sample_rate"] = rate
            sampled_out.append((kept[0], seen - len(kept)))

        return sampled_out

    @staticmethod
    def _merge(reservoirs: "list[_Reservoir]", limit: int, seen: int) -> "list[Metric]":
        """Draw uniform sample of at most `limit` metrics out of all `seen`."""
        bounds = list(itertools.accumulate(reservoir.seen for reservoir in reservoirs))
        slots = [0] * len(reservoirs)
        for position in random.sample(range(seen), min(limit, seen)):
            slots[bisect.bisect_right(bounds, position)] += 1

        kept = []
        for reservoir, count in zip(reservoirs, slots):
            thread_kept = reservoir.kept[:]
            kept.extend(random.sample(thread_kept, min(count, len(thread_kept))))
        return kept

    def _record_lost(self, item: "Metric") -> None:
        envelope_item = None
        with capture_internal_exceptions():
            envelope_item = self._to_envelope_item([item])

        if envelope_item:
            self._record_lost_func(
                reason="queue_overflow",
                data_category=self.CLIENT_REPORT_TYPE,
                item=envelope_item,
                quantity=1,
            )

    @classmethod
    def _to_envelope_item(cls, items: "list[Metric]") -> "Item":
        return Item(
            type=cls.TYPE,
            content_type=cls.CONTENT_TYPE,
            headers={
                "item_count": len(items),
            },
            payload=PayloadRef(
                json={
                    "version": 2,
                    "items": [cls._to_transport_format(item) for item in items],
                }
            ),
        )

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
