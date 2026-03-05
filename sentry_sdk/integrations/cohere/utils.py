from sentry_sdk.ai.utils import set_data_normalized

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


def transitive_getattr(obj, *attrs):
    # type: (Any, str) -> Any
    current = obj
    for attr in attrs:
        current = getattr(current, attr, None)
        if not current:
            return None
    return current


def get_first_from_sources(obj, source_paths, require_truthy=False):
    # type: (Any, list[tuple[str, ...]], bool) -> Any
    for source_path in source_paths:
        value = transitive_getattr(obj, *source_path)
        if value if require_truthy else value is not None:
            return value
    return None


def set_span_data_from_sources(span, obj, target_sources, require_truthy):
    # type: (Any, Any, dict[str, list[tuple[str, ...]]], bool) -> None
    for spandata_key, source_paths in target_sources.items():
        value = get_first_from_sources(obj, source_paths, require_truthy=require_truthy)
        if value is not None:
            set_data_normalized(span, spandata_key, value)
