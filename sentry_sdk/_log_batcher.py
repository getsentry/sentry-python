import os
import random
import threading
from datetime import datetime, timezone
from typing import Optional, List, Callable, TYPE_CHECKING, Any

from sentry_sdk.utils import format_timestamp, safe_repr
from sentry_sdk.envelope import Envelope, Item, PayloadRef

if TYPE_CHECKING:
    from sentry_sdk._types import Log


class LogBatcher:
    MAX_LOGS_BEFORE_FLUSH = 100
    MAX_LOGS_BEFORE_DROP = 1_000
    FLUSH_WAIT_TIME = 5.0

    def __init__(
        self,
        capture_func: "Callable[[Envelope], None]",
        record_lost_func: "Callable[..., None]",
    ) -> None:
        self._log_buffer: "List[Log]" = []
        self._capture_func = capture_func
        self._record_lost_func = record_lost_func
        self._running = True
        self._lock = threading.Lock()

        self._flush_event: "threading.Event" = threading.Event()

        self._flusher: "Optional[threading.Thread]" = None
        self._flusher_pid: "Optional[int]" = None

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

    def _flush_loop(self) -> None:
        while self._running:
            self._flush_event.wait(self.FLUSH_WAIT_TIME + random.random())
            self._flush_event.clear()
            self._flush()

    def add(
        self,
        log: "Log",
    ) -> None:
        if not self._ensure_thread() or self._flusher is None:
            return None

        with self._lock:
            if len(self._log_buffer) >= self.MAX_LOGS_BEFORE_DROP:
                # Construct log envelope item without sending it to report lost bytes
                log_item = Item(
                    type="log",
                    content_type="application/vnd.sentry.items.log+json",
                    headers={
                        "item_count": 1,
                    },
                    payload=PayloadRef(
                        json={"items": [LogBatcher._log_to_transport_format(log)]}
                    ),
                )
                self._record_lost_func(
                    reason="queue_overflow",
                    data_category="log_item",
                    item=log_item,
                    quantity=1,
                )
                return None

            self._log_buffer.append(log)
            if len(self._log_buffer) >= self.MAX_LOGS_BEFORE_FLUSH:
                self._flush_event.set()

    def kill(self) -> None:
        if self._flusher is None:
            return

        self._running = False
        self._flush_event.set()
        self._flusher = None

    def flush(self) -> None:
        self._flush()

    @staticmethod
    def _log_to_transport_format(log: "Log") -> "Any":
        def format_attribute(val: "int | float | str | bool") -> "Any":
            if isinstance(val, bool):
                return {"value": val, "type": "boolean"}
            if isinstance(val, int):
                return {"value": val, "type": "integer"}
            if isinstance(val, float):
                return {"value": val, "type": "double"}
            if isinstance(val, str):
                return {"value": val, "type": "string"}
            return {"value": safe_repr(val), "type": "string"}

        if "sentry.severity_number" not in log["attributes"]:
            log["attributes"]["sentry.severity_number"] = log["severity_number"]
        if "sentry.severity_text" not in log["attributes"]:
            log["attributes"]["sentry.severity_text"] = log["severity_text"]

        res = {
            "timestamp": int(log["time_unix_nano"]) / 1.0e9,
            "trace_id": log.get("trace_id", "00000000-0000-0000-0000-000000000000"),
            "span_id": log.get("span_id"),
            "level": str(log["severity_text"]),
            "body": str(log["body"]),
            "attributes": {
                k: format_attribute(v) for (k, v) in log["attributes"].items()
            },
        }

        return res

    def _flush(self) -> "Optional[Envelope]":
        envelope = Envelope(
            headers={"sent_at": format_timestamp(datetime.now(timezone.utc))}
        )
        with self._lock:
            if len(self._log_buffer) == 0:
                return None

            envelope.add_item(
                Item(
                    type="log",
                    content_type="application/vnd.sentry.items.log+json",
                    headers={
                        "item_count": len(self._log_buffer),
                    },
                    payload=PayloadRef(
                        json={
                            "items": [
                                self._log_to_transport_format(log)
                                for log in self._log_buffer
                            ]
                        }
                    ),
                )
            )
            self._log_buffer.clear()

        self._capture_func(envelope)
        return envelope
