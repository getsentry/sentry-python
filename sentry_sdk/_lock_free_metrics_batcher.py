import math
import os
import random
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sentry_sdk.envelope import Envelope, Item, PayloadRef
from sentry_sdk.utils import (
    capture_internal_exceptions,
    format_timestamp,
    logger,
    serialize_attribute,
)

if TYPE_CHECKING:
    from typing import Any, Callable, Optional, Union

    from sentry_sdk._types import Metric, MetricWindowSamplingOptions

DEFAULT_SAMPLE_PER_SECOND = 2
# Matches FLUSH_WAIT_TIME, so each window goes out in one flush
WINDOW_SECONDS = 5.0


class _Reservoir:
    __slots__ = ("kept", "seen")

    def __init__(self) -> None:
        self.kept: "list[Metric]" = []
        self.seen = 0


class _ThreadState:
    """One thread's reservoirs in a window. Only that thread writes to it, so
    it needs no synchronization."""

    __slots__ = ("reservoirs", "held")

    def __init__(self) -> None:
        self.reservoirs: "dict[str, _Reservoir]" = {}
        # Slots across this thread's reservoirs, to bound memory
        self.held = 0


class _Window:
    __slots__ = ("start", "threads", "retire_claims")

    def __init__(self) -> None:
        self.start = time.monotonic()
        # Thread ident -> that thread's reservoirs. Belongs to the window, so
        # threads that are gone disappear with it
        self.threads: "dict[int, _ThreadState]" = {}
        self.retire_claims: "list[object]" = []


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
        window_sampling: "Union[bool, MetricWindowSamplingOptions, None]" = None,
    ) -> None:
        self._capture_func = capture_func
        self._record_lost_func = record_lost_func

        self._default_limit: "Optional[int]" = None
        self._name_limits: "dict[str, int]" = {}
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

    def _limit(self, name: str) -> "Optional[int]":
        if not self._sampling:
            return None
        return self._name_limits.get(name, self._default_limit)

    def _ensure_thread(self) -> bool:
        """For forking processes we might need to restart this thread.
        This ensures that our process actually has that thread running.
        """
        if not self._running:
            return False

        pid = os.getpid()
        if self._flusher_pid == pid:
            return True

        # dict.setdefault is atomic, so exactly one thread gets to start the flusher
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

        limit = self._limit(item["name"])
        if limit is None:
            kept = self._add_to_queue(item)
        else:
            kept = self._add_to_window(item, limit)

        if not kept:
            self._record_lost(item)

    def _add_to_queue(self, item: "Metric") -> bool:
        queue = self._queue
        # Checking and appending isn't atomic.
        # The queue can go over the limit by at most one metric per concurrently adding thread.
        if len(queue) >= self.MAX_BEFORE_DROP:
            return False

        queue.append(item)
        if len(queue) >= self.MAX_BEFORE_FLUSH and not self._flush_event.is_set():
            self._flush_event.set()
        return True

    def _add_to_window(self, item: "Metric", limit: int) -> bool:
        if time.monotonic() - self._window.start >= WINDOW_SECONDS:
            self._retire_window(force=False)

        # A thread that's still writing when its window is harvested loses
        # that metric. It has almost always been counted already, so it's
        # reported as sampled out.
        window = self._window
        thread_id = threading.get_ident()
        state = window.threads.get(thread_id)
        if state is None:
            state = window.threads.setdefault(thread_id, _ThreadState())

        # Reservoir sampling (Algorithm R) on this thread's own reservoir
        name = item["name"]
        reservoir = state.reservoirs.get(name)
        if reservoir is None:
            reservoir = state.reservoirs[name] = _Reservoir()
        if len(reservoir.kept) < limit:
            if state.held >= self.MAX_BEFORE_DROP:
                # Out of memory budget. Not seen, so it doesn't count towards
                # the sample rate
                return False
            state.held += 1
            reservoir.seen += 1
            reservoir.kept.append(item)
        else:
            reservoir.seen += 1
            index = random.randrange(reservoir.seen)
            if index < limit:
                reservoir.kept[index] = item
        return True

    def _retire_window(self, force: bool) -> None:
        window = self._window
        if not force and time.monotonic() - window.start < WINDOW_SECONDS:
            # Early flushes leave the window open, so a name can't go over its
            # rate
            return

        claim = object()
        window.retire_claims.append(claim)
        if window.retire_claims[0] is not claim:
            # Someone else is retiring this window
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
                # Another consumer drained it in the meantime
                break

        sampled_out: "list[tuple[Metric, int]]" = []
        if self._sampling:
            # Retire before taking the retired windows, so a flush() sends the
            # window it just retired
            self._retire_window(force)
            for _ in range(len(self._retired)):
                try:
                    window = self._retired.popleft()
                except IndexError:
                    # Another consumer took it
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
            # Serializing every sampled out metric would cost as much as we saved
            # by sampling, so we estimate from one
            with capture_internal_exceptions():
                self._record_lost_func(
                    reason="sample_rate",
                    data_category="trace_metric_byte",
                    quantity=count * len(self._to_envelope_item([item]).get_bytes()),
                )

        return envelope

    def _harvest(
        self, window: "_Window", items: "list[Metric]"
    ) -> "list[tuple[Metric, int]]":
        # Copies, since a thread that's still writing may add to them
        by_name: "dict[str, list[_Reservoir]]" = {}
        for state in window.threads.copy().values():
            for name, reservoir in state.reservoirs.copy().items():
                by_name.setdefault(name, []).append(reservoir)

        sampled_out = []
        for name, reservoirs in by_name.items():
            limit = self._limit(name)
            if limit is None:
                continue

            seen = sum(reservoir.seen for reservoir in reservoirs)
            kept = self._merge(reservoirs, limit, seen)
            if not kept:
                continue

            items.extend(kept)
            if seen <= len(kept):
                # Nothing was sampled out
                continue

            rate = len(kept) / seen
            for item in kept:
                attributes = item["attributes"]
                client_rate = attributes.get("sentry.client_sample_rate")
                if isinstance(client_rate, (int, float)) and 0 < client_rate <= 1:
                    attributes["sentry.client_sample_rate"] = rate * client_rate
                else:
                    attributes["sentry.client_sample_rate"] = rate
            sampled_out.append((kept[0], seen - len(kept)))

        return sampled_out

    @staticmethod
    def _merge(reservoirs: "list[_Reservoir]", limit: int, seen: int) -> "list[Metric]":
        """Merge per-thread samples of one name into one uniform sample of at
        most ``limit`` metrics out of all ``seen``."""
        # Split the slots across threads like drawing without replacement from
        # all seen metrics, so each thread gets slots in proportion to how much
        # it saw
        left = [reservoir.seen for reservoir in reservoirs]
        remaining = seen
        slots = [0] * len(reservoirs)
        for _ in range(min(limit, seen)):
            pick = random.randrange(remaining)
            i = 0
            for i, count in enumerate(left):
                if pick < count:
                    break
                pick -= count
            slots[i] += 1
            left[i] -= 1
            remaining -= 1

        # A random subset of a uniform sample is still a uniform sample
        kept = []
        for reservoir, count in zip(reservoirs, slots):
            thread_kept = reservoir.kept[:]
            # A thread caught writing can have counted a metric it hasn't
            # stored yet
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
