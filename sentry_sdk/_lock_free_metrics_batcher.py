"""
Experimental lock-free metrics batcher.

Producers (``add``) never take a lock. Shared state is only changed through
operations that are atomic on builtin containers, both with the GIL and on
free-threaded CPython:

- ``deque.append`` / ``deque.popleft``
- ``dict.setdefault``
- ``next()`` on an ``itertools.count``
- rebinding an attribute

The flusher thread is the only consumer: it's the only thread that removes
metrics from the queue, so the front of the queue only moves when it pops.
That lets batches be defined by position. Every add takes the next position,
and the add that completes a batch of ``MAX_BEFORE_FLUSH`` registers its
position and wakes the flusher. The flusher knows how many metrics it has
removed so far, so it removes exactly the ones up to that position. Metrics
added in the meantime wait for the next batch.

An explicit ``flush()`` doesn't drain the queue itself. It asks the flusher to
send everything and waits for it to finish, so when it returns, everything
added before it has been handed to the transport.

This is independent of ``Batcher`` on purpose, so it can evolve (e.g. gain
sampling) without affecting the other batchers.

Like the other batchers, it doesn't register its own fork hook. Its owner must
call ``_reset_thread_state()`` in a forked child.
"""

import itertools
import os
import random
import threading
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sentry_sdk.envelope import Envelope, Item, PayloadRef
from sentry_sdk.utils import (
    capture_internal_exceptions,
    format_timestamp,
    serialize_attribute,
)

if TYPE_CHECKING:
    from typing import Any, Callable, Deque, Dict, List, Optional

    from sentry_sdk._types import Metric


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
    ) -> None:
        self._capture_func = capture_func
        self._record_lost_func = record_lost_func
        self._reset_thread_state()

    def _reset_thread_state(self) -> None:
        self._queue: "Deque[Metric]" = deque()
        # Positions handed out to adds, in the order they were added
        self._positions = itertools.count()
        # Positions that completed a batch, for the flusher to flush up to
        self._flush_targets: "Deque[int]" = deque()
        # Explicit flushes waiting for the flusher to send everything
        self._flush_requests: "Deque[threading.Event]" = deque()
        # How many metrics have been removed from the queue. Only the consumer
        # writes it, so it doesn't need to be atomic
        self._drained = 0
        self._running = True
        self._active: "threading.local" = threading.local()
        self._flush_event: "threading.Event" = threading.Event()
        self._flusher: "Optional[threading.Thread]" = None
        self._flusher_pid: "Optional[int]" = None
        # Holds the claim of the one thread that gets to start the flusher. A
        # forked child gets a fresh one when its owner resets this batcher
        self._flusher_claim: "Dict[str, object]" = {}

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
            # Clear before reading the requests and targets, so that one
            # registered after this point wakes the next iteration
            self._flush_event.clear()

            requests = _drain(self._flush_requests)
            targets = _drain(self._flush_targets)
            try:
                # A failure to serialize or send one batch must not kill the
                # flusher thread, or every later metric would be dropped.
                with capture_internal_exceptions():
                    if requests or not targets:
                        # Explicit flush, or woken by the timer: send everything
                        self._flush(until=None)
                    else:
                        # Woken by a full batch: send up to the add that
                        # completed it
                        self._flush(until=max(targets))
            finally:
                for request in requests:
                    request.set()

        # Don't leave flush() calls that came in while stopping waiting for
        # their timeout
        for request in _drain(self._flush_requests):
            request.set()

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

            queue = self._queue
            # Checking and appending isn't atomic, so the queue can go over the
            # limit by at most one metric per concurrently adding thread
            if len(queue) >= self.MAX_BEFORE_DROP:
                self._record_lost(item)
                return None

            position = next(self._positions)
            queue.append(item)
            if (position + 1) % self.MAX_BEFORE_FLUSH == 0:
                # Positions are unique, so exactly one add completes each batch.
                # It registers the batch and leaves the sending to the flusher
                self._flush_targets.append(position + 1)
                self._flush_event.set()
        finally:
            self._active.flag = False

    def kill(self) -> None:
        if self._flusher is None:
            return

        self._running = False
        self._flush_event.set()
        self._flusher = None

    def flush(self) -> None:
        if self._flusher is None and not self._queue:
            # Nothing to send, so no reason to start the flusher for it
            return

        if threading.current_thread() is self._flusher or not self._ensure_thread():
            # There's no other consumer to hand off to: this is the flusher
            # itself, flushing re-entrantly, or the flusher was killed or can't
            # be started. Send everything from this thread
            was_active = getattr(self._active, "flag", False)
            self._active.flag = True
            try:
                self._flush(until=None)
            finally:
                self._active.flag = was_active
            return

        done = threading.Event()
        self._flush_requests.append(done)
        self._flush_event.set()
        done.wait(self.FLUSH_TIMEOUT)

    def _flush(self, until: "Optional[int]") -> "Optional[Envelope]":
        """Send the queued metrics up to position ``until``, or all of them.

        Must only be called by the consumer."""
        queue = self._queue
        count = len(queue) if until is None else until - self._drained

        items: "List[Metric]" = []
        for _ in range(count):
            try:
                items.append(queue.popleft())
            except IndexError:
                # An add took its position but hasn't appended yet. Its metric
                # goes out with the next batch
                break
        self._drained += len(items)

        if not items:
            return None

        envelope = Envelope(
            headers={"sent_at": format_timestamp(datetime.now(timezone.utc))}
        )
        envelope.add_item(self._to_envelope_item(items))
        self._capture_func(envelope)
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


def _drain(items: "Deque[Any]") -> "List[Any]":
    drained = []
    while True:
        try:
            drained.append(items.popleft())
        except IndexError:
            return drained
