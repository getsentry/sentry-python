import os
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sentry_sdk._batcher import Batcher
from sentry_sdk.envelope import Envelope
from sentry_sdk.utils import format_timestamp, serialize_attribute

if TYPE_CHECKING:
    from typing import Any, Callable, Optional

    from sentry_sdk._types import Log


class LogBatcher(Batcher["Log"]):
    MAX_BEFORE_FLUSH = 100
    MAX_BEFORE_DROP = 1_000
    FLUSH_WAIT_TIME = 5.0

    TYPE = "log"
    CLIENT_REPORT_TYPE = "log_item"
    CONTENT_TYPE = "application/vnd.sentry.items.log+json"

    def __init__(
        self,
        capture_func: "Callable[[Envelope], None]",
        record_lost_func: "Callable[..., None]",
    ) -> None:
        super().__init__(capture_func, record_lost_func)
        self._lock = threading.Lock()

    def _reset_thread_state(self) -> None:
        super()._reset_thread_state()
        self._lock = threading.Lock()

    def _ensure_thread(self) -> bool:
        """For forking processes we might need to restart this thread.
        This ensures that our process actually has that thread running.
        """
        if not self._running:
            return False

        pid = os.getpid()
        if self._flusher_pid == pid:
            return True

        with self._lock:
            # Recheck to make sure another thread didn't get here and start the
            # the flusher in the meantime
            if self._flusher_pid == pid:
                return True

            self._flusher_pid = pid

            self._flusher = threading.Thread(target=self._flush_loop)
            self._flusher.daemon = True

            try:
                self._flusher.start()
            except RuntimeError:
                # Unfortunately at this point the interpreter is in a state that no
                # longer allows us to spawn a thread and we have to bail.
                self._running = False
                return False

        return True

    def add(self, item: "Log") -> None:
        # Bail out if the current thread is already executing batcher code.
        # This prevents deadlocks when code running inside the batcher (e.g.
        # _add_to_envelope during flush, or _flush_event.wait/set) triggers
        # a GC-emitted warning that routes back through the logging
        # integration into add().
        if getattr(self._active, "flag", False):
            return None

        self._active.flag = True
        try:
            if not self._ensure_thread() or self._flusher is None:
                return None

            with self._lock:
                dropped = len(self._buffer) >= self.MAX_BEFORE_DROP
                if not dropped:
                    self._buffer.append(item)
                    if len(self._buffer) >= self.MAX_BEFORE_FLUSH:
                        self._flush_event.set()

            # Record the loss outside of the lock, since this might involve
            # serializing the item to report lost bytes
            if dropped:
                self._record_lost(item)
        finally:
            self._active.flag = False

    def _flush(self) -> "Optional[Envelope]":
        envelope = Envelope(
            headers={"sent_at": format_timestamp(datetime.now(timezone.utc))}
        )
        with self._lock:
            if len(self._buffer) == 0:
                return None

            self._add_to_envelope(envelope)
            self._buffer.clear()

        self._capture_func(envelope)
        return envelope

    @staticmethod
    def _to_transport_format(item: "Log") -> "Any":
        if "sentry.severity_number" not in item["attributes"]:
            item["attributes"]["sentry.severity_number"] = item["severity_number"]
        if "sentry.severity_text" not in item["attributes"]:
            item["attributes"]["sentry.severity_text"] = item["severity_text"]

        res = {
            "timestamp": int(item["time_unix_nano"]) / 1.0e9,
            "level": str(item["severity_text"]),
            "body": str(item["body"]),
            "attributes": {
                k: serialize_attribute(v) for (k, v) in item["attributes"].items()
            },
        }

        if item.get("trace_id") is not None:
            res["trace_id"] = item["trace_id"]

        if item.get("span_id") is not None:
            res["span_id"] = item["span_id"]

        return res
