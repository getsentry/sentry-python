"""
The API in this file is only meant to be used in span streaming mode.

You can enable span streaming mode via
sentry_sdk.init(_experiments={"trace_lifecycle": "stream"}).
"""

import uuid
from typing import TYPE_CHECKING

from sentry_sdk.utils import format_attribute

if TYPE_CHECKING:
    from typing import Optional
    from sentry_sdk._types import Attributes, AttributeValue


class StreamedSpan:
    """
    A span holds timing information of a block of code.

    Spans can have multiple child spans thus forming a span tree.

    This is the Span First span implementation. The original transaction-based
    span implementation lives in tracing.Span.
    """

    __slots__ = (
        "name",
        "_attributes",
        "_trace_id",
    )

    def __init__(
        self,
        *,
        name: str,
        attributes: "Optional[Attributes]" = None,
        trace_id: "Optional[str]" = None,
    ):
        self.name: str = name
        self._attributes: "Attributes" = {}
        if attributes:
            for attribute, value in attributes.items():
                self.set_attribute(attribute, value)

        self._trace_id = trace_id

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
    def trace_id(self) -> str:
        if not self._trace_id:
            self._trace_id = uuid.uuid4().hex

        return self._trace_id
