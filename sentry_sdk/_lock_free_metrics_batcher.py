"""
Experimental lock-free metrics batcher with window sampling, enabled with the
``metrics_window_sampling`` experiment.

Producers (``add``) never take a lock. Shared state is only changed through
operations that are atomic on builtin containers, both with the GIL and on
free-threaded CPython:

- ``deque.append`` / ``deque.popleft``
- ``list.append`` and storing to an existing list index
- ``dict.get`` / ``dict.setdefault`` / ``dict.copy``
- ``next()`` on an ``itertools.count``
- rebinding an attribute

The flusher thread is the only consumer of the queue: it's the only thread
that removes items from it, so the queue can't shrink under it. Each pass
removes as many items as the queue held when the pass started. Items added in
the meantime wait for the next pass, so a pass always ends, even under constant
load.

Starting the flusher thread and retiring a sampling window need several of the
operations above to happen together, so they're decided by a "first one wins"
claim instead of a lock.

Window sampling keeps at most ``limit`` metrics per metric name per window with
reservoir sampling (Algorithm R), where every step is one of the atomic
operations above. A window is retired by rebinding ``self._window`` to a new
one. A thread may still hold a reference to the old window and be about to
write to it, so retired windows are only harvested after a grace period, giving
those writes time to land (the same idea as RCU).

An explicit ``flush()`` doesn't drain the queue itself. It appends a sentinel
(a ``threading.Event``) to the queue, wakes the flusher and waits. Everything
the calling thread added before ``flush()`` is ahead of the sentinel, so once
the flusher has sent the pass that contained it and set it, those metrics have
been handed to the transport. Concurrent flushes are answered by the same pass.
A pass that answers a flush harvests every window immediately, since flushes
are used at shutdown and by callers who want everything sent now.

This is independent of ``Batcher`` on purpose, so it can evolve without
affecting the other batchers.

Like the other batchers, it doesn't register its own fork hook. Its owner must
call ``_reset_thread_state()`` in a forked child.
"""

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
    from typing import Any, Callable, Deque, Dict, List, Optional, Tuple, Union

    from sentry_sdk._types import Metric, MetricWindowSamplingOptions

DEFAULT_SAMPLE_PER_SECOND = 2
# Matches FLUSH_WAIT_TIME, so each window goes out in one flush
WINDOW_SECONDS = 5.0
# How long a retired window is left alone before it's harvested, so that adds
# which picked it up right before it was retired can finish writing to it
RETIRE_GRACE_SECONDS = 1.0


class _Reservoir:
    __slots__ = ("kept", "seen", "overflowed")

    def __init__(self) -> None:
        self.kept: "List[Metric]" = []
        self.seen = itertools.count()
        # Metrics that were seen but couldn't get a slot because the window ran
        # out of memory budget. They're reported as queue_overflow, so they must
        # not also count towards the sample rate
        self.overflowed = itertools.count()


class _Window:
    __slots__ = ("start", "reservoirs", "held", "retired_at", "retire_claims")

    def __init__(self) -> None:
        self.start = time.monotonic()
        self.reservoirs: "Dict[str, _Reservoir]" = {}
        # Slots handed out across all reservoirs, to bound memory
        self.held = itertools.count()
        self.retired_at = math.inf
        self.retire_claims: "List[object]" = []


class LockFreeMetricsBatcher:
    MAX_BEFORE_FLUSH = 1000
    MAX_BEFORE_DROP = 10_000
    FLUSH_WAIT_TIME = 5.0
    # How long flush() waits for the flusher to send everything
    FLUSH_TIMEOUT = 2.0

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

        self._reset_thread_state()

    def _reset_thread_state(self) -> None:
        # Metrics, and sentinels of explicit flushes waiting for the metrics
        # ahead of them to be sent
        self._queue: "Deque[Union[Metric, threading.Event]]" = deque()
        self._window = _Window()
        self._retired: "Deque[_Window]" = deque()
        self._running = True
        self._active: "threading.local" = threading.local()
        self._flush_event: "threading.Event" = threading.Event()
        self._flusher: "Optional[threading.Thread]" = None
        self._flusher_pid: "Optional[int]" = None
        # Holds the claim of the one thread that gets to start the flusher. A
        # forked child gets a fresh one when its owner resets this batcher
        self._flusher_claim: "Dict[str, object]" = {}

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

        # A plain attribute can't be claimed safely, since checking and setting
        # it are two steps. dict.setdefault does both atomically, so exactly one
        # thread gets to start the flusher
        claim = object()
        if self._flusher_claim.setdefault("flusher", claim) is not claim:
            # Another thread is starting the flusher. Metrics are queued until
            # it's up, so there's no need to wait for it
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
        # Mark the flush-loop thread as active for its entire lifetime so that
        # any re-entrant add() triggered while flushing is dropped instead of
        # feeding back into the batcher.
        self._active.flag = True
        while self._running:
            self._flush_event.wait(self.FLUSH_WAIT_TIME + random.random())
            # Clear before the pass reads the queue's length, so that a metric
            # or sentinel added after that point wakes the next iteration
            self._flush_event.clear()
            self._flush()

        # Send what's left, including the sampling windows, and answer the
        # flush() calls that came in while stopping, instead of leaving them
        # waiting for their timeout
        self._flush(force=True)

    def add(self, item: "Metric") -> None:
        # Bail out if the current thread is already executing batcher code,
        # e.g. a metric emitted from inside a flush.
        if getattr(self._active, "flag", False):
            return None

        self._active.flag = True
        try:
            # Not checking self._flusher here: a thread that lost the race to
            # start the flusher sees it unset for a moment, and its metric should
            # still be queued
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

            # Record the loss outside of the hot path, since this serializes
            # the metric to report lost bytes
            if not kept:
                self._record_lost(item)
        finally:
            self._active.flag = False

    def _add_to_queue(self, item: "Metric") -> bool:
        queue = self._queue
        # Checking and appending isn't atomic, so the queue can go over the
        # limit by at most one metric per concurrently adding thread
        if len(queue) >= self.MAX_BEFORE_DROP:
            return False

        queue.append(item)
        if len(queue) >= self.MAX_BEFORE_FLUSH and not self._flush_event.is_set():
            self._flush_event.set()
        return True

    def _add_to_window(self, item: "Metric", limit: int) -> bool:
        now = time.monotonic()
        if now - self._window.start >= WINDOW_SECONDS:
            self._retire_window(now, force=False)

        window = self._window
        name = item["name"]
        reservoir = window.reservoirs.get(name)
        if reservoir is None:
            reservoir = window.reservoirs.setdefault(name, _Reservoir())

        seen = next(reservoir.seen)
        if seen < limit:
            # Replacing a slot in a full reservoir doesn't need more memory, only
            # a new slot does
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
                # The slot hasn't been filled yet by a concurrent add, or never
                # will be because that add overflowed. Either way this metric is
                # sampled out, which keeps the reservoir within its memory budget
                pass
        return True

    def kill(self) -> None:
        if self._flusher is None:
            return

        self._running = False
        self._flush_event.set()
        self._flusher = None

    def flush(self) -> None:
        if (
            self._flusher is None
            and not self._queue
            and not self._window.reservoirs
            and not self._retired
        ):
            # Nothing to send, so no reason to start the flusher for it
            return

        if threading.current_thread() is self._flusher or not self._ensure_thread():
            # There's no other consumer to hand off to: this is the flusher
            # itself, flushing re-entrantly, or the flusher was killed or can't
            # be started. Send everything from this thread
            was_active = getattr(self._active, "flag", False)
            self._active.flag = True
            try:
                self._flush(force=True)
            finally:
                self._active.flag = was_active
            return

        done = threading.Event()
        # Append before waking the flusher, so the pass it wakes for either
        # sees the sentinel or is followed by another one that does
        self._queue.append(done)
        self._flush_event.set()
        done.wait(self.FLUSH_TIMEOUT)

    def _retire_window(self, now: float, force: bool) -> None:
        window = self._window
        if not force and now - window.start < WINDOW_SECONDS:
            # Early flushes leave the window open, so a name can't go over its
            # rate
            return

        claim = object()
        window.retire_claims.append(claim)
        if window.retire_claims[0] is not claim:
            # Someone else is retiring this window
            return

        self._window = _Window()
        window.retired_at = now
        self._retired.append(window)

    def _take_retired_windows(self, now: float, force: bool) -> "List[_Window]":
        ready = []
        waiting = []
        while True:
            try:
                window = self._retired.popleft()
            except IndexError:
                break
            if force or now - window.retired_at >= RETIRE_GRACE_SECONDS:
                ready.append(window)
            else:
                waiting.append(window)

        # Put back the windows that are still in their grace period, oldest first
        self._retired.extendleft(reversed(waiting))
        return ready

    def _harvest(
        self, window: "_Window", items: "List[Metric]"
    ) -> "List[Tuple[Metric, int]]":
        sampled_out = []
        for reservoir in window.reservoirs.copy().values():
            kept = reservoir.kept
            if not kept:
                continue

            items.extend(kept)

            # The counters are dead once a window is harvested, so reading them
            # by advancing them is fine
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

    def _flush(self, force: bool = False) -> "Optional[Envelope]":
        """Send the metrics that were queued when this pass started and the
        sampling windows that are ready, then answer the flush() calls among
        them. A pass that answers a flush, or is forced, harvests every window.

        Must only be called by the consumer."""
        queue = self._queue
        items: "List[Metric]" = []
        requests: "List[threading.Event]" = []
        for _ in range(len(queue)):
            try:
                item = queue.popleft()
            except IndexError:
                # Drained by a flush() from a thread that took over after the
                # flusher was killed
                break
            if isinstance(item, threading.Event):
                requests.append(item)
            else:
                items.append(item)

        envelope = None
        try:
            # A failure to serialize or send one batch must not kill the
            # flusher thread, or every later metric would be dropped.
            with capture_internal_exceptions():
                sampled_out: "List[Tuple[Metric, int]]" = []
                if self._sampling:
                    force = force or bool(requests)
                    now = time.monotonic()
                    # Take the already retired windows before retiring the
                    # current one, so a window always gets its grace period
                    # unless forced
                    ready = self._take_retired_windows(now, force)
                    self._retire_window(now, force)
                    if force:
                        ready.extend(self._take_retired_windows(now, force))
                    for window in ready:
                        sampled_out.extend(self._harvest(window, items))

                if items:
                    envelope = Envelope(
                        headers={
                            "sent_at": format_timestamp(datetime.now(timezone.utc))
                        }
                    )
                    envelope.add_item(self._to_envelope_item(items))
                    self._capture_func(envelope)

                for item, count in sampled_out:
                    self._record_lost_func(
                        reason="sample_rate",
                        data_category=self.CLIENT_REPORT_TYPE,
                        quantity=count,
                    )
                    # Serializing every sampled out metric would cost as much as
                    # we saved by sampling, so we estimate from one
                    with capture_internal_exceptions():
                        self._record_lost_func(
                            reason="sample_rate",
                            data_category="trace_metric_byte",
                            quantity=count
                            * len(self._to_envelope_item([item]).get_bytes()),
                        )
        finally:
            for request in requests:
                request.set()

        return envelope

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
    def _to_envelope_item(cls, items: "List[Metric]") -> "Item":
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
