"""
Experimental lock-free metrics batcher.

Producers (``add``) never take a lock. Shared state is only changed through
operations that are atomic on builtin containers, both with the GIL and on
free-threaded CPython:

- ``deque.append`` / ``deque.popleft``
- ``dict.setdefault``
- rebinding an attribute

The flusher thread is the only consumer: it's the only thread that removes
items from the queue, so the queue can't shrink under it. Each pass removes
as many items as the queue held when the pass started. Items added in the
meantime wait for the next pass, so a pass always ends, even under constant
load.

An explicit ``flush()`` doesn't drain the queue itself. It appends a sentinel
(a ``threading.Event``) to the queue, wakes the flusher and waits. Everything
the calling thread added before ``flush()`` is ahead of the sentinel, so once
the flusher has sent the pass that contained it and set it, those metrics have
been handed to the transport. Concurrent flushes are answered by the same pass.

This is independent of ``Batcher`` on purpose, so it can evolve (e.g. gain
sampling) without affecting the other batchers.

Like the other batchers, it doesn't register its own fork hook. Its owner must
call ``_reset_thread_state()`` in a forked child.
"""

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
    from typing import Any, Callable, Deque, Dict, List, Optional, Union

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
        # Metrics, and sentinels of explicit flushes waiting for the metrics
        # ahead of them to be sent
        self._queue: "Deque[Union[Metric, threading.Event]]" = deque()
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
            # Clear before the pass reads the queue's length, so that a metric
            # or sentinel added after that point wakes the next iteration
            self._flush_event.clear()
            self._flush()

        # Answer the flush() calls that came in while stopping, instead of
        # leaving them waiting for their timeout
        self._flush()

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

            queue.append(item)
            if len(queue) >= self.MAX_BEFORE_FLUSH and not self._flush_event.is_set():
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
                self._flush()
            finally:
                self._active.flag = was_active
            return

        done = threading.Event()
        # Append before waking the flusher, so the pass it wakes for either
        # sees the sentinel or is followed by another one that does
        self._queue.append(done)
        self._flush_event.set()
        done.wait(self.FLUSH_TIMEOUT)

    def _flush(self) -> "Optional[Envelope]":
        """Send the metrics that were queued when this pass started, then
        answer the flush() calls among them.

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
                if items:
                    envelope = Envelope(
                        headers={
                            "sent_at": format_timestamp(datetime.now(timezone.utc))
                        }
                    )
                    envelope.add_item(self._to_envelope_item(items))
                    self._capture_func(envelope)
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
