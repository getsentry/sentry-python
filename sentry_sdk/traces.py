"""
The API in this file is only meant to be used in span streaming mode.

You can enable span streaming mode via
sentry_sdk.init(_experiments={"trace_lifecycle": "stream"}).
"""

import uuid
from enum import Enum
from typing import TYPE_CHECKING

from sentry_sdk.utils import format_attribute, logger

if TYPE_CHECKING:
    from typing import Optional, Union
    from sentry_sdk._types import Attributes, AttributeValue


class SpanStatus(str, Enum):
    OK = "ok"
    ERROR = "error"

    def __str__(self) -> str:
        return self.value


# Segment source, see
# https://getsentry.github.io/sentry-conventions/generated/attributes/sentry.html#sentryspansource
class SegmentSource(str, Enum):
    COMPONENT = "component"
    CUSTOM = "custom"
    ROUTE = "route"
    TASK = "task"
    URL = "url"
    VIEW = "view"

    def __str__(self) -> str:
        return self.value


# These are typically high cardinality and the server hates them
LOW_QUALITY_SEGMENT_SOURCES = [
    SegmentSource.URL,
]


SOURCE_FOR_STYLE = {
    "endpoint": SegmentSource.COMPONENT,
    "function_name": SegmentSource.COMPONENT,
    "handler_name": SegmentSource.COMPONENT,
    "method_and_path_pattern": SegmentSource.ROUTE,
    "path": SegmentSource.URL,
    "route_name": SegmentSource.COMPONENT,
    "route_pattern": SegmentSource.ROUTE,
    "uri_template": SegmentSource.ROUTE,
    "url": SegmentSource.ROUTE,
}


class StreamedSpan:
    """
    A span holds timing information of a block of code.

    Spans can have multiple child spans, thus forming a span tree.

    This is the Span First span implementation that streams spans. The original
    transaction-based span implementation lives in tracing.Span.
    """

    __slots__ = (
        "_name",
        "_attributes",
        "_active",
        "_span_id",
        "_trace_id",
        "_status",
    )

    def __init__(
        self,
        *,
        name: str,
        attributes: "Optional[Attributes]" = None,
        active: bool = True,
        trace_id: "Optional[str]" = None,
    ):
        self._name: str = name
        self._active: bool = active
        self._attributes: "Attributes" = {}
        if attributes:
            for attribute, value in attributes.items():
                self.set_attribute(attribute, value)

        self._span_id: "Optional[str]" = None
        self._trace_id: "Optional[str]" = trace_id

        self._status = SpanStatus.OK.value
        self.set_attribute("sentry.span.source", SegmentSource.CUSTOM.value)

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}("
            f"name={self._name}, "
            f"trace_id={self.trace_id}, "
            f"span_id={self.span_id}, "
            f"active={self._active})>"
        )

    def get_attributes(self) -> "Attributes":
        return self._attributes

    def set_attribute(self, key: str, value: "AttributeValue") -> None:
        self._attributes[key] = format_attribute(value)

    def set_attributes(self, attributes: "Attributes") -> None:
        for key, value in attributes.items():
            self.set_attribute(key, value)

    def remove_attribute(self, key: str) -> None:
        try:
            del self._attributes[key]
        except KeyError:
            pass

    @property
    def status(self) -> "str":
        return self._status

    @status.setter
    def status(self, status: "Union[SpanStatus, str]") -> None:
        if isinstance(status, Enum):
            status = status.value

        if status not in {e.value for e in SpanStatus}:
            logger.debug(
                f'Unsupported span status {status}. Expected one of: "ok", "error"'
            )
            return

        self._status = status

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, name: str) -> None:
        self._name = name

    @property
    def active(self) -> bool:
        return self._active

    @property
    def span_id(self) -> str:
        if not self._span_id:
            self._span_id = uuid.uuid4().hex[16:]

        return self._span_id

    @property
    def trace_id(self) -> str:
        if not self._trace_id:
            self._trace_id = uuid.uuid4().hex

        return self._trace_id


class NoOpStreamedSpan(StreamedSpan):
    def get_attributes(self) -> "Attributes":
        return {}

    def set_attribute(self, key: str, value: "AttributeValue") -> None:
        pass

    def set_attributes(self, attributes: "Attributes") -> None:
        pass

    def remove_attribute(self, key: str) -> None:
        pass

    @property
    def status(self) -> "str":
        return SpanStatus.OK.value

    @status.setter
    def status(self, status: "Union[SpanStatus, str]") -> None:
        pass

    @property
    def name(self) -> str:
        return ""

    @name.setter
    def name(self, value: str) -> None:
        pass

    @property
    def active(self) -> bool:
        return True

    @property
    def span_id(self) -> str:
        return "0000000000000000"

    @property
    def trace_id(self) -> str:
        return "00000000000000000000000000000000"
