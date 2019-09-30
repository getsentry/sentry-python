import contextlib
import itertools

from datetime import datetime

from sentry_sdk.utils import (
    AnnotatedValue,
    capture_internal_exceptions,
    safe_repr,
    strip_string,
)

from sentry_sdk._compat import text_type, PY2, string_types, number_types, iteritems

from sentry_sdk._types import MYPY

if MYPY:
    from typing import Any
    from typing import Dict
    from typing import List
    from typing import Tuple
    from typing import Optional
    from typing import Callable
    from typing import Union
    from typing import Generator

    from sentry_sdk._types import NotImplementedType

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
    def __init__(self):
        # type: () -> None
        self._inner = {}  # type: Dict[int, Any]

    @contextlib.contextmanager
    def memoize(self, obj):
        # type: (Any) -> Generator[bool, None, None]
        if id(obj) in self._inner:
            yield True
        else:
            self._inner[id(obj)] = obj
            yield False

            self._inner.pop(id(obj), None)


class Serializer(object):
    __slots__ = ("memo", "_path", "_meta_stack", "_is_databag", "_should_repr_strings")

    def __init__(self):
        # type: () -> None
        self.memo = Memo()

        self._path = []  # type: List[Segment]
        self._meta_stack = []  # type: List[Dict[Segment, Any]]
        self._is_databag = None  # type: Optional[bool]
        self._should_repr_strings = None  # type: Optional[bool]

    def startswith_path(self, path):
        # type: (Tuple[Optional[Segment], ...]) -> bool
        if len(path) > len(self._path):
            return False

        for i, segment in enumerate(path):
            if segment is None:
                continue

            if self._path[i] != segment:
                return False

        return True

    def annotate(self, **meta):
        # type: (**Any) -> None
        while len(self._meta_stack) <= len(self._path):
            try:
                segment = self._path[len(self._meta_stack) - 1]
                node = self._meta_stack[-1].setdefault(text_type(segment), {})
            except IndexError:
                node = {}

            self._meta_stack.append(node)

        self._meta_stack[-1].setdefault("", {}).update(meta)

    def should_repr_strings(self):
        # type: () -> bool
        if self._should_repr_strings is None:
            self._should_repr_strings = (
                self.startswith_path(
                    ("exception", "values", None, "stacktrace", "frames", None, "vars")
                )
                or self.startswith_path(
                    ("threads", "values", None, "stacktrace", "frames", None, "vars")
                )
                or self.startswith_path(("stacktrace", "frames", None, "vars"))
            )

        return self._should_repr_strings

    def is_databag(self):
        # type: () -> bool
        if self._is_databag is None:
            self._is_databag = (
                self.should_repr_strings()
                or self.startswith_path(("request", "data"))
                or self.startswith_path(("breadcrumbs", None))
                or self.startswith_path(("extra",))
            )

        return self._is_databag

    def serialize_event(self, obj):
        # type: (Any) -> Dict[str, Any]
        rv = self._serialize_node(obj)
        if self._meta_stack:
            rv["_meta"] = self._meta_stack[0]
        return rv

    def _serialize_node(self, obj, max_depth=None, max_breadth=None, segment=None):
        # type: (Any, Optional[int], Optional[int], Optional[Segment]) -> Any
        if segment is not None:
            self._path.append(segment)
            self._is_databag = self._is_databag or None
            self._should_repr_strings = self._should_repr_strings or None

        try:
            with capture_internal_exceptions():
                with self.memo.memoize(obj) as result:
                    if result:
                        return CYCLE_MARKER

                    return self._serialize_node_impl(
                        obj, max_depth=max_depth, max_breadth=max_breadth
                    )

            if self.is_databag():
                return u"<failed to serialize, use init(debug=True) to see error logs>"

            return None
        finally:
            if segment is not None:
                self._path.pop()
                del self._meta_stack[len(self._path) + 1 :]
                self._is_databag = self._is_databag and None
                self._should_repr_strings = self._should_repr_strings and None

    def _flatten_annotated(self, obj):
        # type: (Any) -> Any
        if isinstance(obj, AnnotatedValue):
            self.annotate(**obj.metadata)
            obj = obj.value
        return obj

    def _serialize_node_impl(self, obj, max_depth, max_breadth):
        # type: (Any, Optional[int], Optional[int]) -> Any
        cur_depth = len(self._path)
        if max_depth is None and max_breadth is None and self.is_databag():
            max_depth = cur_depth + MAX_DATABAG_DEPTH
            max_breadth = cur_depth + MAX_DATABAG_BREADTH

        if max_depth is None:
            remaining_depth = None
        else:
            remaining_depth = max_depth - cur_depth

        obj = self._flatten_annotated(obj)

        if remaining_depth is not None and remaining_depth <= 0:
            self.annotate(rem=[["!limit", "x"]])
            if self.is_databag():
                return self._flatten_annotated(strip_string(safe_repr(obj)))
            return None

        if global_repr_processors and self.is_databag():
            hints = {"memo": self.memo, "remaining_depth": remaining_depth}
            for processor in global_repr_processors:
                with capture_internal_exceptions():
                    result = processor(obj, hints)
                    if result is not NotImplemented:
                        return self._flatten_annotated(result)

        if isinstance(obj, Mapping):
            # Create temporary copy here to avoid calling too much code that
            # might mutate our dictionary while we're still iterating over it.
            if max_breadth is not None and len(obj) >= max_breadth:
                rv_dict = dict(itertools.islice(iteritems(obj), None, max_breadth))
                self.annotate(len=len(obj))
            else:
                rv_dict = dict(iteritems(obj))

            for k in list(rv_dict):
                str_k = text_type(k)
                v = self._serialize_node(
                    rv_dict.pop(k),
                    max_depth=max_depth,
                    max_breadth=max_breadth,
                    segment=str_k,
                )
                if v is not None:
                    rv_dict[str_k] = v

            return rv_dict
        elif not isinstance(obj, string_types) and isinstance(obj, Sequence):
            if max_breadth is not None and len(obj) >= max_breadth:
                rv_list = list(obj)[:max_breadth]
                self.annotate(len=len(obj))
            else:
                rv_list = list(obj)

            for i in range(len(rv_list)):
                rv_list[i] = self._serialize_node(
                    rv_list[i], max_depth=max_depth, max_breadth=max_breadth, segment=i
                )

            return rv_list

        if self.should_repr_strings():
            obj = safe_repr(obj)
        else:
            if obj is None or isinstance(obj, (bool, number_types)):
                return obj

            if isinstance(obj, datetime):
                return text_type(obj.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))

            if isinstance(obj, bytes):
                obj = obj.decode("utf-8", "replace")

            if not isinstance(obj, string_types):
                obj = safe_repr(obj)

        return self._flatten_annotated(strip_string(obj))
