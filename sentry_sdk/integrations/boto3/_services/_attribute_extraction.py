from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, Optional, Sequence, Tuple

    from sentry_sdk._types import Attributes

    _Converter = Callable[[Any], Optional[Any]]
    # e.g. ("Limit", "aws.dynamodb.limit", _as_integer) converts
    # {"Limit": 10} into {"aws.dynamodb.limit": 10} using `_extract_attributes()`
    _AttributeSpec = Tuple[str, str, _Converter]


def _as_integer(value: "Any") -> "Optional[int]":
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _as_string(value: "Any") -> "Optional[str]":
    return value if isinstance(value, str) and value else None


def _extract_attributes(
    source: "Any", specs: "Sequence[_AttributeSpec]"
) -> "Attributes":
    if not isinstance(source, dict):
        return {}

    attributes = {}
    for param, attribute, convert in specs:
        value = convert(source.get(param))
        # an unexpected type results in that attribute being omitted.
        if value is not None:
            attributes[attribute] = value
    return attributes
