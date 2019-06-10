from copy import copy
from collections import deque
from functools import wraps
from itertools import chain

from sentry_sdk.utils import logger, capture_internal_exceptions

if False:
    from typing import Any
    from typing import Dict
    from typing import Optional
    from typing import Deque
    from typing import List
    from typing import Callable
    from typing import TypeVar

    from sentry_sdk.utils import Breadcrumb, Event, EventProcessor, ErrorProcessor, Hint

    F = TypeVar("F", bound=Callable[..., Any])


global_event_processors = []  # type: List[EventProcessor]


def add_global_event_processor(processor):
    # type: (EventProcessor) -> None
    global_event_processors.append(processor)


def _attr_setter(fn):
    return property(fset=fn, doc=fn.__doc__)


def _disable_capture(fn):
    # type: (F) -> F
    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        # type: (Any, *Dict[str, Any], **Any) -> Any
        if not self._should_capture:
            return
        try:
            self._should_capture = False
            return fn(self, *args, **kwargs)
        finally:
            self._should_capture = True

    return wrapper  # type: ignore


class Scope(object):
    """The scope holds extra information that should be sent with all
    events that belong to it.
    """

    __slots__ = (
        "_level",
        "_name",
        "_fingerprint",
        "_transaction",
        "_user",
        "_tags",
        "_contexts",
        "_extras",
        "_breadcrumbs",
        "_event_processors",
        "_error_processors",
        "_should_capture",
        "_span",
    )

    def __init__(self):
        # type: () -> None
        self._event_processors = []  # type: List[EventProcessor]
        self._error_processors = []  # type: List[ErrorProcessor]

        self._name = None  # type: Optional[str]
        self.clear()

    @_attr_setter
    def level(self, value):
        """When set this overrides the level."""
        self._level = value

    @_attr_setter
    def fingerprint(self, value):
        """When set this overrides the default fingerprint."""
        self._fingerprint = value

    @_attr_setter
    def transaction(self, value):
        """When set this forces a specific transaction name to be set."""
        self._transaction = value

    @_attr_setter
    def user(self, value):
        """When set a specific user is bound to the scope."""
        self._user = value

    def set_span_context(self, span_context):
        """Sets the span context."""
        self._span = span_context

    def set_tag(self, key, value):
        # type: (str, Any) -> None
        """Sets a tag for a key to a specific value."""
        self._tags[key] = value

    def remove_tag(self, key):
        # type: (str) -> None
        """Removes a specific tag."""
        self._tags.pop(key, None)

    def set_context(self, key, value):
        # type: (str, Any) -> None
        """Binds a context at a certain key to a specific value."""
        self._contexts[key] = value

    def remove_context(self, key):
        # type: (str) -> None
        """Removes a context."""
        self._contexts.pop(key, None)

    def set_extra(self, key, value):
        # type: (str, Any) -> None
        """Sets an extra key to a specific value."""
        self._extras[key] = value

    def remove_extra(self, key):
        # type: (str) -> None
        """Removes a specific extra key."""
        self._extras.pop(key, None)

    def clear(self):
        # type: () -> None
        """Clears the entire scope."""
        self._level = None
        self._fingerprint = None
        self._transaction = None
        self._user = None

        self._tags = {}  # type: Dict[str, Any]
        self._contexts = {}  # type: Dict[str, Dict[str, Any]]
        self._extras = {}  # type: Dict[str, Any]

        self.clear_breadcrumbs()
        self._should_capture = True

        self._span = None

    def clear_breadcrumbs(self):
        # type: () -> None
        """Clears breadcrumb buffer."""
        self._breadcrumbs = deque()  # type: Deque[Breadcrumb]

    def add_event_processor(self, func):
        # type: (EventProcessor) -> None
        """"Register a scope local event processor on the scope.

        This function behaves like `before_send.`
        """
        self._event_processors.append(func)

    def add_error_processor(self, func, cls=None):
        # type: (ErrorProcessor, Optional[type]) -> None
        """"Register a scope local error processor on the scope.

        The error processor works similar to an event processor but is
        invoked with the original exception info triple as second argument.
        """
        if cls is not None:
            cls_ = cls  # For mypy.
            real_func = func

            def func(event, exc_info):
                try:
                    is_inst = isinstance(exc_info[1], cls_)
                except Exception:
                    is_inst = False
                if is_inst:
                    return real_func(event, exc_info)
                return event

        self._error_processors.append(func)

    @_disable_capture
    def apply_to_event(self, event, hint):
        # type: (Event, Hint) -> Optional[Event]
        """Applies the information contained on the scope to the given event."""

        def _drop(event, cause, ty):
            # type: (Dict[str, Any], Any, str) -> Optional[Any]
            logger.info("%s (%s) dropped event (%s)", ty, cause, event)
            return None

        if self._level is not None:
            event["level"] = self._level

        event.setdefault("breadcrumbs", []).extend(self._breadcrumbs)
        if event.get("user") is None and self._user is not None:
            event["user"] = self._user

        if event.get("transaction") is None and self._transaction is not None:
            event["transaction"] = self._transaction

        if event.get("fingerprint") is None and self._fingerprint is not None:
            event["fingerprint"] = self._fingerprint

        if self._extras:
            event.setdefault("extra", {}).update(self._extras)

        if self._tags:
            event.setdefault("tags", {}).update(self._tags)

        if self._contexts:
            event.setdefault("contexts", {}).update(self._contexts)

        if self._span is not None:
            event.setdefault("contexts", {})["trace"] = {
                "trace_id": self._span.trace_id,
                "span_id": self._span.span_id,
            }

        exc_info = hint.get("exc_info")
        if exc_info is not None:
            for error_processor in self._error_processors:
                new_event = error_processor(event, exc_info)
                if new_event is None:
                    return _drop(event, error_processor, "error processor")
                event = new_event

        for event_processor in chain(global_event_processors, self._event_processors):
            new_event = event
            with capture_internal_exceptions():
                new_event = event_processor(event, hint)
            if new_event is None:
                return _drop(event, event_processor, "event processor")
            event = new_event

        return event

    def __copy__(self):
        # type: () -> Scope
        rv = object.__new__(self.__class__)  # type: Scope

        rv._level = self._level
        rv._name = self._name
        rv._fingerprint = self._fingerprint
        rv._transaction = self._transaction
        rv._user = self._user

        rv._tags = dict(self._tags)
        rv._contexts = dict(self._contexts)
        rv._extras = dict(self._extras)

        rv._breadcrumbs = copy(self._breadcrumbs)
        rv._event_processors = list(self._event_processors)
        rv._error_processors = list(self._error_processors)

        rv._should_capture = self._should_capture
        rv._span = self._span

        return rv

    def __repr__(self):
        # type: () -> str
        return "<%s id=%s name=%s>" % (
            self.__class__.__name__,
            hex(id(self)),
            self._name,
        )
