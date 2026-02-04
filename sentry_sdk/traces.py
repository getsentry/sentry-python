"""
The API in this file is only meant to be used in span streaming mode.

You can enable span streaming mode via
sentry_sdk.init(_experiments={"trace_lifecycle": "stream"}).
"""

import uuid
from enum import Enum
from typing import TYPE_CHECKING

from sentry_sdk.consts import SPANDATA
from sentry_sdk.utils import format_attribute

if TYPE_CHECKING:
    from typing import Optional, Union
    from sentry_sdk._types import Attributes, AttributeValue


FLAGS_CAPACITY = 10


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

    Spans can have multiple child spans thus forming a span tree.

    This is the Span First span implementation. The original transaction-based
    span implementation lives in tracing.Span.
    """

    __slots__ = (
        "_name",
        "_attributes",
        "_span_id",
        "_trace_id",
        "_status",
        "_flags",
    )

    def __init__(
        self,
        *,
        name: str,
        attributes: "Optional[Attributes]" = None,
        trace_id: "Optional[str]" = None,
    ):
        self._name: str = name
        self._attributes: "Attributes" = {}
        if attributes:
            for attribute, value in attributes.items():
                self.set_attribute(attribute, value)

        self._span_id: "Optional[str]" = None
        self._trace_id: "Optional[str]" = trace_id

        self.set_status(SpanStatus.OK)
        self.set_source(SegmentSource.CUSTOM)

        self._flags: dict[str, bool] = {}

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

    def get_status(self) -> "Union[SpanStatus, str]":
        if self._status in {s.value for s in SpanStatus}:
            return SpanStatus(self._status)

        return self._status

    def set_status(self, status: "Union[SpanStatus, str]") -> None:
        if isinstance(status, Enum):
            status = status.value

        self._status = status

    def set_http_status(self, http_status: int) -> None:
        self.set_attribute(SPANDATA.HTTP_STATUS_CODE, http_status)

        if http_status >= 400:
            self.set_status(SpanStatus.ERROR)
        else:
            self.set_status(SpanStatus.OK)

    def get_name(self) -> str:
        return self._name

    def set_name(self, name: str) -> None:
        self._name = name

    def set_flag(self, flag: str, result: bool) -> None:
        if len(self._flags) < FLAGS_CAPACITY:
            self._flags[flag] = result

    def set_op(self, op: str) -> None:
        self.set_attribute("sentry.op", op)

    def set_origin(self, origin: str) -> None:
        self.set_attribute("sentry.origin", origin)

    def set_source(self, source: "Union[str, SegmentSource]") -> None:
        if isinstance(source, Enum):
            source = source.value

        self.set_attribute("sentry.span.source", source)

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
