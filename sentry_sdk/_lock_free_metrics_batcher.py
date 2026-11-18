import itertools
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
# Marks a window's writers once it's retired
_RETIRED = "retired"


class _Reservoir:
    __slots__ = ("kept", "seen", "overflowed")

    def __init__(self) -> None:
        self.kept: "list[Metric]" = []
        self.seen = itertools.count()
        # Metrics that were seen but couldn't get a slot because the window ran
        # out of memory budget. They're reported as queue_overflow, so they must
        # not also count towards the sample rate
        self.overflowed = itertools.count()


class _Window:
    __slots__ = ("start", "reservoirs", "held", "retire_claims", "writers")

    def __init__(self) -> None:
        self.start = time.monotonic()
        self.reservoirs: "dict[str, _Reservoir]" = {}
        # Slots handed out across all reservoirs, to bound memory
        self.held = itertools.count()
        self.retire_claims: "list[object]" = []
        # Adds currently writing to this window, plus the _RETIRED mark once
        # it's retired. Both live in one dict, so registering and checking for
        # the mark can't be reordered against setting it.
        self.writers: "dict[object, bool]" = {}


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

        limit = (
            self._name_limits.get(item["name"], self._default_limit)
            if self._sampling
            else None
        )
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

        # Register before touching the window. A consumer harvests a retired
        # window only once every registered add has left, and an add that
        # registers after the window was retired sees the mark and moves on to
        # the new window. So every metric is either harvested or in a later
        # window.
        token = object()
        while True:
            window = self._window
            writers = window.writers
            writers[token] = True
            if _RETIRED not in writers:
                break
            del writers[token]

        try:
            name = item["name"]
            reservoir = window.reservoirs.get(name)
            if reservoir is None:
                reservoir = window.reservoirs.setdefault(name, _Reservoir())

            seen = next(reservoir.seen)
            if seen < limit:
                # Replacing a slot in a full reservoir doesn't need more memory,
                # only a new slot does
                if next(window.held) >= self.MAX_BEFORE_DROP:
                    next(reservoir.overflowed)
                    return False
                reservoir.kept.append(item)
                return True

            index = random.randrange(seen + 1)
            if index < limit:
                try:
                    reservoir.kept[index] = item
                except IndexError:
                    # The slot hasn't been filled yet by a concurrent add, or
                    # never will be because that add overflowed. Either way this
                    # metric is sampled out, which keeps the reservoir within
                    # its memory budget
                    pass
            return True
        finally:
            del writers[token]

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

        # Swap before marking, so that adds turned away by the mark find the
        # new window right away
        self._window = _Window()
        window.writers[_RETIRED] = True
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
                # Adds that registered before the window was retired are only
                # a few bytecodes from leaving
                while len(window.writers) > 1:
                    time.sleep(0)
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
        sampled_out = []
        for reservoir in window.reservoirs.values():
            kept = reservoir.kept
            if not kept:
                continue

            items.extend(kept)

            # Nothing writes to a harvested window, so reading the counters by
            # advancing them is fine
            seen = next(reservoir.seen) - next(reservoir.overflowed)
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
