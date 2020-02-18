import uuid
from datetime import datetime

from sentry_sdk._compat import text_type
from sentry_sdk._types import MYPY, SessionStatus

if MYPY:
    from typing import Optional
    from typing import Union
    from typing import Any


DID_UUID = uuid.uuid5(uuid.NAMESPACE_URL, "https://sentry.io/#did")


def _make_did(
    did  # type: text_type
):
    # type: (...) -> uuid.UUID
    try:
        return uuid.UUID(did)
    except ValueError:
        return uuid.uuid5(DID_UUID, did)


def _timestamp(
    dt  # type: datetime
):
    # type: (...) -> str
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Session(object):
    def __init__(
        self,
        sid=None,  # type: Optional[Union[text_type, uuid.UUID]]
        did=None,  # type: Optional[Union[text_type, uuid.UUID]]
        timestamp=None,  # type: Optional[datetime]
        started=None,  # type: Optional[datetime]
        duration=None,  # type: Optional[float]
        status=None,  # type: Optional[SessionStatus]
        release=None,  # type: Optional[str]
        environment=None,  # type: Optional[str]
    ):
        # type: (...) -> None
        if sid is None:
            sid = uuid.uuid4()
        if started is None:
            started = datetime.utcnow()
        if status is None:
            status = "ok"
        self.seq = 0
        self.started = started
        self.update(
            sid=sid,
            did=did,
            timestamp=timestamp,
            duration=duration,
            release=release,
            environment=environment,
        )

    def update(
        self,
        sid=None,  # type: Optional[Union[text_type, uuid.UUID]]
        did=None,  # type: Optional[Union[text_type, uuid.UUID]]
        timestamp=None,  # type: Optional[datetime]
        duration=None,  # type: Optional[float]
        status=None,  # type: Optional[SessionStatus]
        release=None,  # type: Optional[str]
        environment=None,  # type: Optional[str]
    ):
        # type: (...) -> None
        if sid is not None:
            self.sid = sid
        if did is not None:
            self.did = did
        if timestamp is None:
            timestamp = datetime.utcnow()
        self.timestamp = timestamp
        if duration is not None:
            self.duration = duration
        if status is not None:
            self.status = status
        if release is not None:
            self.release = release
        if environment is not None:
            self.environment = environment

        # any session update bumps this
        self.seq += 1

    def to_json(self):
        # type: (...) -> Any
        rv = {
            "sid": str(self.sid),
            "started": _timestamp(self.started),
            "timestamp": _timestamp(self.timestamp),
            "status": self.status,
        }
        attrs = {}
        if self.did is not None:
            rv["did"] = str(self.did)
        if self.duration is not None:
            rv["duration"] = self.duration
        if self.release is not None:
            attrs["release"] = self.release
        if self.environment is not None:
            attrs["environment"] = self.environment
        if attrs:
            rv["attrs"] = attrs
        return rv
