"""
Experimental lock-free metrics batcher.

Producers (``add``) and consumers (the flusher thread and explicit ``flush``)
never take a lock. Shared state is only changed through operations that are
atomic on builtin containers, both with the GIL and on free-threaded CPython:

- ``deque.append`` / ``deque.popleft``
- ``dict.setdefault``
- rebinding an attribute

This is independent of ``Batcher`` on purpose, so it can evolve (e.g. gain
sampling) without affecting the other batchers.
"""

import os
import random
import threading
import weakref
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

        # See https://github.com/getsentry/sentry-python/blob/051cc01640a29bfd64b1f1e2e3414c02f027dd1b/sentry_sdk/monitor.py#L41-L50
        if hasattr(os, "register_at_fork"):
            weak_reset = weakref.WeakMethod(self._reset_thread_state)

            def _reset_in_child() -> None:
                method = weak_reset()
                if method is not None:
                    method()

            os.register_at_fork(after_in_child=_reset_in_child)

    def _reset_thread_state(self) -> None:
        self._queue: "Deque[Metric]" = deque()
        self._running = True
        self._active: "threading.local" = threading.local()
        self._flush_event: "threading.Event" = threading.Event()
        self._flusher: "Optional[threading.Thread]" = None
        self._flusher_pid: "Optional[int]" = None
        # pid -> token of the thread that gets to start the flusher in that
        # process
        self._flusher_claims: "Dict[int, object]" = {}

    def _ensure_thread(self) -> bool:
        """For forking processes we might need to restart this thread.
        This ensures that our process actually has that thread running.
        """
        if not self._running:
            return False

        pid = os.getpid()
        if self._flusher_pid == pid:
            return True

        # dict.setdefault is atomic, so exactly one thread per process gets to
        # start the flusher
        token = object()
        if self._flusher_claims.setdefault(pid, token) is not token:
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
            self._flush_event.clear()
            # A failure to serialize or send one batch must not kill the
            # flusher thread, or every later metric would be dropped.
            with capture_internal_exceptions():
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
        was_active = getattr(self._active, "flag", False)
        self._active.flag = True
        try:
            self._flush()
        finally:
            self._active.flag = was_active

    def _flush(self) -> "Optional[Envelope]":
        items: "List[Metric]" = []
        queue = self._queue
        for _ in range(len(queue)):
            try:
                items.append(queue.popleft())
            except IndexError:
                # Drained concurrently by another flush
                break

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
