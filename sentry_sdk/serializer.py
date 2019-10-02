import sys
import itertools

from datetime import datetime

from sentry_sdk.utils import (
    AnnotatedValue,
    capture_internal_exception,
    disable_capture_event,
    safe_repr,
    strip_string,
)

from sentry_sdk._compat import text_type, PY2, string_types, number_types, iteritems

from sentry_sdk._types import MYPY

if MYPY:
    from types import TracebackType

    import sentry_sdk

    from typing import Any
    from typing import Dict
    from typing import List
    from typing import Tuple
    from typing import Optional
    from typing import Callable
    from typing import Union
    from typing import ContextManager
    from typing import Type

    from sentry_sdk._types import NotImplementedType, Event

    ReprProcessor = Callable[[Any, Dict[str, Any]], Union[NotImplementedType, str]]
    Segment = Union[str, int]


if PY2:
    # Importing ABCs from collections is deprecated, and will stop working in 3.8
    # https://github.com/python/cpython/blob/master/Lib/collections/__init__.py#L49
    from collections import Mapping, Sequence
else:
    # New in 3.3
    # https://docs.python.org/3/library/collections.abc.html
    from collections.abc import Mapping, Sequence

MAX_DATABAG_DEPTH = 5
MAX_DATABAG_BREADTH = 10
CYCLE_MARKER = u"<cyclic>"


global_repr_processors = []  # type: List[ReprProcessor]


def add_global_repr_processor(processor):
    # type: (ReprProcessor) -> None
    global_repr_processors.append(processor)


class Memo(object):
    __slots__ = ("_inner", "_objs")

    def __init__(self):
        # type: () -> None
        self._inner = {}  # type: Dict[int, Any]
        self._objs = []  # type: List[Any]

    def memoize(self, obj):
        # type: (Any) -> ContextManager[bool]
        self._objs.append(obj)
        return self

    def __enter__(self):
        # type: () -> bool
        obj = self._objs[-1]
        if id(obj) in self._inner:
            return True
        else:
            self._inner[id(obj)] = obj
            return False

    def __exit__(
        self,
        ty,  # type: Optional[Type[BaseException]]
        value,  # type: Optional[BaseException]
        tb,  # type: Optional[TracebackType]
    ):
        # type: (...) -> None
        self._inner.pop(id(self._objs.pop()), None)


def serialize(event, **kwargs):
    # type: (Event, **Any) -> Event
    memo = Memo()
    path = []  # type: List[Segment]
    meta_stack = []  # type: List[Dict[str, Any]]

    def _annotate(**meta):
        # type: (**Any) -> None
        while len(meta_stack) <= len(path):
            try:
                segment = path[len(meta_stack) - 1]
                node = meta_stack[-1].setdefault(text_type(segment), {})
            except IndexError:
                node = {}

            meta_stack.append(node)

        meta_stack[-1].setdefault("", {}).update(meta)

    def _startswith_path(prefix):
        # type: (Tuple[Optional[Segment], ...]) -> bool
        if len(prefix) > len(path):
            return False

        for i, segment in enumerate(prefix):
            if segment is None:
                continue

            if path[i] != segment:
                return False

        return True

    def _serialize_node(
        obj,  # type: Any
        max_depth=None,  # type: Optional[int]
        max_breadth=None,  # type: Optional[int]
        is_databag=None,  # type: Optional[bool]
        should_repr_strings=None,  # type: Optional[bool]
        segment=None,  # type: Optional[Segment]
    ):
        # type: (...) -> Any
        if segment is not None:
            path.append(segment)

        try:
            with memo.memoize(obj) as result:
                if result:
                    return CYCLE_MARKER

                return _serialize_node_impl(
                    obj,
                    max_depth=max_depth,
                    max_breadth=max_breadth,
                    is_databag=is_databag,
                    should_repr_strings=should_repr_strings,
                )
        except BaseException:
            capture_internal_exception(sys.exc_info())

            if is_databag:
                return u"<failed to serialize, use init(debug=True) to see error logs>"

            return None
        finally:
            if segment is not None:
                path.pop()
                del meta_stack[len(path) + 1 :]

    def _flatten_annotated(obj):
        # type: (Any) -> Any
        if isinstance(obj, AnnotatedValue):
            _annotate(**obj.metadata)
            obj = obj.value
        return obj

    def _serialize_node_impl(
        obj, max_depth, max_breadth, is_databag, should_repr_strings
    ):
        # type: (Any, Optional[int], Optional[int], Optional[bool], Optional[bool]) -> Any
        if not should_repr_strings:
            should_repr_strings = (
                _startswith_path(
                    ("exception", "values", None, "stacktrace", "frames", None, "vars")
                )
                or _startswith_path(
                    ("threads", "values", None, "stacktrace", "frames", None, "vars")
                )
                or _startswith_path(("stacktrace", "frames", None, "vars"))
            )

        if obj is None or isinstance(obj, (bool, number_types)):
            return obj if not should_repr_strings else safe_repr(obj)

        if isinstance(obj, datetime):
            return (
                text_type(obj.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
                if not should_repr_strings
                else safe_repr(obj)
            )

        if not is_databag:
            is_databag = (
                should_repr_strings
                or _startswith_path(("request", "data"))
                or _startswith_path(("breadcrumbs", None))
                or _startswith_path(("extra",))
            )

        cur_depth = len(path)
        if max_depth is None and max_breadth is None and is_databag:
            max_depth = cur_depth + MAX_DATABAG_DEPTH
            max_breadth = cur_depth + MAX_DATABAG_BREADTH

        if max_depth is None:
            remaining_depth = None
        else:
            remaining_depth = max_depth - cur_depth

        obj = _flatten_annotated(obj)

        if remaining_depth is not None and remaining_depth <= 0:
            _annotate(rem=[["!limit", "x"]])
            if is_databag:
                return _flatten_annotated(strip_string(safe_repr(obj)))
            return None

        if global_repr_processors and is_databag:
            hints = {"memo": memo, "remaining_depth": remaining_depth}
            for processor in global_repr_processors:
                result = processor(obj, hints)
                if result is not NotImplemented:
                    return _flatten_annotated(result)

        if isinstance(obj, Mapping):
            # Create temporary copy here to avoid calling too much code that
            # might mutate our dictionary while we're still iterating over it.
            if max_breadth is not None and len(obj) >= max_breadth:
                rv_dict = dict(itertools.islice(iteritems(obj), None, max_breadth))
                _annotate(len=len(obj))
            else:
                if type(obj) is dict:
                    rv_dict = dict(obj)
                else:
                    rv_dict = dict(iteritems(obj))

            for k in list(rv_dict):
                str_k = text_type(k)
                v = _serialize_node(
                    rv_dict.pop(k),
                    max_depth=max_depth,
                    max_breadth=max_breadth,
                    segment=str_k,
                    should_repr_strings=should_repr_strings,
                    is_databag=is_databag,
                )
                if v is not None:
                    rv_dict[str_k] = v

            return rv_dict
        elif not isinstance(obj, string_types) and isinstance(obj, Sequence):
            if max_breadth is not None and len(obj) >= max_breadth:
                rv_list = list(obj)[:max_breadth]
                _annotate(len=len(obj))
            else:
                rv_list = list(obj)

            for i in range(len(rv_list)):
                rv_list[i] = _serialize_node(
                    rv_list[i],
                    max_depth=max_depth,
                    max_breadth=max_breadth,
                    segment=i,
                    should_repr_strings=should_repr_strings,
                    is_databag=is_databag,
                )

            return rv_list

        if should_repr_strings:
            obj = safe_repr(obj)
        else:
            if isinstance(obj, bytes):
                obj = obj.decode("utf-8", "replace")

            if not isinstance(obj, string_types):
                obj = safe_repr(obj)

        return _flatten_annotated(strip_string(obj))

    disable_capture_event.set(True)
    try:
        rv = _serialize_node(event, **kwargs)
        if meta_stack and isinstance(rv, dict):
            rv["_meta"] = meta_stack[0]
        return rv
    finally:
        disable_capture_event.set(False)


def partial_serialize(client, data, should_repr_strings=True, is_databag=True):
    # type: (Optional[sentry_sdk.Client], Any, bool, bool) -> Any
    is_recursive = disable_capture_event.get(None)
    if is_recursive:
        return CYCLE_MARKER

    if client is not None and client.options["_experiments"].get(
        "fast_serialize", False
    ):
        data = serialize(
            data, should_repr_strings=should_repr_strings, is_databag=is_databag
        )

        if isinstance(data, dict):
            # TODO: Bring back _meta annotations
            data.pop("_meta", None)
        return data

    return data
