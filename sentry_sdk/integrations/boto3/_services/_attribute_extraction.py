from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

    _Converter = Callable[[Any], Optional[Any]]
    # e.g. ("Limit", "aws.dynamodb.limit", _as_integer) converts
    # {"Limit": 10} into {"aws.dynamodb.limit": 10} using `_extract_attributes()`
    _AttributeSpec = Tuple[str, str, _Converter]


def _as_string(value: "Any") -> "Optional[str]":
    return value if isinstance(value, str) and value else None


def _as_boolean(value: "Any") -> "Optional[bool]":
    return value if isinstance(value, bool) else None


def _as_integer(value: "Any") -> "Optional[int]":
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _as_string_list(value: "Any") -> "Optional[List[str]]":
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    return None


def _list_length(value: "Any") -> "Optional[int]":
    return len(value) if isinstance(value, list) else None


def _extract_attributes(
    source: "Any", specs: "Sequence[_AttributeSpec]"
) -> "Dict[str, Any]":
    if not isinstance(source, dict):
        return {}

    attributes = {}
    for param, attribute, convert in specs:
        value = convert(source.get(param))
        # an unexpected type results in that attribute being omitted.
        if value is not None:
            attributes[attribute] = value
    return attributes
