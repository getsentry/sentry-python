from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Dict, Sequence, Tuple

    from sentry_sdk._types import Attributes

    # tuple of (request param name, span attribute name).
    _AttributeSpec = Tuple[str, str]


def _extract_attributes(
    source: "Dict[str, Any]", specs: "Sequence[_AttributeSpec]"
) -> "Attributes":
    attributes = {}
    for param, attribute in specs:
        if param in source:
            attributes[attribute] = source[param]
    return attributes
