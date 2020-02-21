import os
import uuid
import time
from datetime import datetime
from threading import Thread, Lock
from weakref import ref as weakref

from sentry_sdk._compat import text_type
from sentry_sdk._types import MYPY

if MYPY:
    from typing import Optional
    from typing import Union
    from typing import Any
    from typing import Dict

    from sentry_sdk._types import SessionStatus
    from sentry_sdk.hub import Hub


def auto_start_session():
    # type: (...) -> None
    from sentry_sdk.hub import Hub

    hub = Hub.current
    if hub.client and hub.client.options["auto_session_tracking"]:
        hub.start_session()


def auto_stop_session():
    # type: (...) -> None
    from sentry_sdk.hub import Hub

    hub = Hub.current
    if hub.client and hub.client.options["auto_session_tracking"]:
        hub.stop_session()


def _timestamp(
    dt,  # type: datetime
):
    # type: (...) -> str
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _make_uuid(
    val,  # type: Union[str, uuid.UUID]
):
    # type: (...) -> uuid.UUID
    if isinstance(val, uuid.UUID):
        return val
    return uuid.UUID(val)


TERMINAL_SESSION_STATES = ("exited", "abnormal", "crashed")


class SessionFlusher(object):
    def __init__(
        self,
        flush_func,  # type: Any
        flush_interval=10,  # type: int
    ):
        # type: (...) -> None
        self.flush_func = flush_func
        self.flush_interval = flush_interval
        self.pending = {}  # type: Dict[str, Any]
        self._thread = None  # type: Optional[Thread]
        self._thread_lock = Lock()
        self._thread_for_pid = None  # type: Optional[int]
        self._running = True

    def flush(self):
        # type: (...) -> None
        pending = self.pending
        self.pending = {}
        self.flush_func(list(pending.values()))

    def _ensure_running(self):
        # type: (...) -> None
        if self._thread_for_pid == os.getpid() and self._thread is not None:
            return None
        with self._thread_lock:
            if self._thread_for_pid == os.getpid() and self._thread is not None:
                return None

            def _thread():
                # type: (...) -> None
                while self._running:
                    time.sleep(self.flush_interval)
                    if self.pending and self._running:
                        self.flush()

            thread = Thread(target=_thread)
            thread.daemon = True
            thread.start()
            self._thread = thread
            self._thread_for_pid = os.getpid()
        return None

    def add_session(
        self, session  # type: Session
    ):
        # type: (...) -> None
        self.pending[session.sid.hex] = session.to_json()
        self._ensure_running()

    def kill(self):
        # type: (...) -> None
        self._running = False

    def __del__(self):
        # type: (...) -> None
        self.kill()


class Session(object):
    def __init__(
        self,
        sid=None,  # type: Optional[Union[str, uuid.UUID]]
        did=None,  # type: Optional[str]
        timestamp=None,  # type: Optional[datetime]
        started=None,  # type: Optional[datetime]
        duration=None,  # type: Optional[float]
        status=None,  # type: Optional[SessionStatus]
        release=None,  # type: Optional[str]
        environment=None,  # type: Optional[str]
        user=None,  # type: Optional[Any]
        hub=None,  # type: Optional[Hub]
        user_agent=None,  # type: Optional[str]
        ip_address=None,  # type: Optional[str]
    ):
        # type: (...) -> None
        self._hub = weakref(hub)
        if sid is None:
            sid = uuid.uuid4()
        if started is None:
            started = datetime.utcnow()
        if status is None:
            status = "ok"
        self.status = status
        self.did = None  # type: Optional[str]
        self.seq = 0
        self.started = started
        self.release = None  # type: Optional[str]
        self.environment = None  # type: Optional[str]
        self.duration = None  # type: Optional[float]
        self.user_agent = None  # type: Optional[str]
        self.ip_address = None  # type: Optional[str]

        self.update(
            sid=sid,
            did=did,
            timestamp=timestamp,
            duration=duration,
            release=release,
            environment=environment,
            user=user,
            user_agent=user_agent,
            ip_address=ip_address,
        )

    def update(
        self,
        sid=None,  # type: Optional[Union[str, uuid.UUID]]
        did=None,  # type: Optional[str]
        timestamp=None,  # type: Optional[datetime]
        duration=None,  # type: Optional[float]
        status=None,  # type: Optional[SessionStatus]
        release=None,  # type: Optional[str]
        environment=None,  # type: Optional[str]
        user=None,  # type: Optional[Any]
        user_agent=None,  # type: Optional[str]
        ip_address=None,  # type: Optional[str]
    ):
        # type: (...) -> None
        if user is not None:
            if did is None:
                did = user.get("id") or user.get("email") or user.get("username")
                if did is not None:
                    did = text_type(did)
            if ip_address is None:
                ip_address = user.get("ip_address")

        hub = self._hub()
        options = hub.client.options if hub and hub.client else None

        if sid is not None:
            self.sid = _make_uuid(sid)
        if did is not None:
            self.did = did
        if timestamp is None:
            timestamp = datetime.utcnow()
        self.timestamp = timestamp
        if duration is not None:
            self.duration = duration
        if release is None and options:
            release = options["release"]
        if release is not None:
            self.release = release
        if environment is None and options:
            environment = options["environment"]
        if environment is not None:
            self.environment = environment
        if ip_address is not None:
            self.ip_address = ip_address
        if user_agent is not None:
            self.user_agent = user_agent
        self.duration = (datetime.utcnow() - self.started).total_seconds()

        # status can only transition in certain ways
        if status is not None:
            if self.status is None:
                self.status = status
            elif status == "degraded" or status in TERMINAL_SESSION_STATES:
                if self.status not in TERMINAL_SESSION_STATES:
                    self.status = status

        # any session update bumps this
        self.seq += 1

        # propagate session changes to the hub
        if hub is not None:
            client = hub.client
            if client is not None:
                client.capture_session(self)

    def close(
        self, status=None  # type: Optional[SessionStatus]
    ):
        # type: (...) -> Any
        if status is None and self.status == "ok":
            status = "exited"
        if status is not None:
            self.update(status=status)

    def to_json(self):
        # type: (...) -> Any
        rv = {
            "sid": str(self.sid),
            "started": _timestamp(self.started),
            "timestamp": _timestamp(self.timestamp),
            "status": self.status,
        }  # type: Dict[str, Any]
        attrs = {}
        if self.did is not None:
            rv["did"] = self.did
        if self.duration is not None:
            rv["duration"] = self.duration
        if self.release is not None:
            attrs["release"] = self.release
        if self.environment is not None:
            attrs["environment"] = self.environment
        if self.ip_address is not None:
            attrs["ip_address"] = self.ip_address
        if self.user_agent is not None:
            attrs["user_agent"] = self.user_agent
        if attrs:
            rv["attrs"] = attrs
        return rv
