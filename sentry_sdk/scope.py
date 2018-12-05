from copy import copy
from collections import deque
from itertools import chain

from sentry_sdk.utils import logger, capture_internal_exceptions


global_event_processors = []


def add_global_event_processor(processor):
    global_event_processors.append(processor)


def _attr_setter(fn):
    return property(fset=fn, doc=fn.__doc__)


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
    )

    def __init__(self):
        self._event_processors = []
        self._error_processors = []

        self._name = None
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

    def set_tag(self, key, value):
        """Sets a tag for a key to a specific value."""
        self._tags[key] = value

    def remove_tag(self, key):
        """Removes a specific tag."""
        self._tags.pop(key, None)

    def set_context(self, key, value):
        """Binds a context at a certain key to a specific value."""
        self._contexts[key] = value

    def remove_context(self, key):
        """Removes a context."""
        self._contexts.pop(key, None)

    def set_extra(self, key, value):
        """Sets an extra key to a specific value."""
        self._extras[key] = value

    def remove_extra(self, key):
        """Removes a specific extra key."""
        self._extras.pop(key, None)

    def clear(self):
        """Clears the entire scope."""
        self._level = None
        self._fingerprint = None
        self._transaction = None
        self._user = None

        self._tags = {}
        self._contexts = {}
        self._extras = {}

        self._breadcrumbs = deque()

    def add_event_processor(self, func):
        """"Register a scope local event processor on the scope.

        This function behaves like `before_send.`
        """
        self._event_processors.append(func)

    def add_error_processor(self, func, cls=None):
        """"Register a scope local error processor on the scope.

        The error processor works similar to an event processor but is
        invoked with the original exception info triple as second argument.
        """
        if cls is not None:
            real_func = func

            def func(event, exc_info):
                try:
                    is_inst = isinstance(exc_info[1], cls)
                except Exception:
                    is_inst = False
                if is_inst:
                    return real_func(event, exc_info)
                return event

        self._error_processors.append(func)

    def apply_to_event(self, event, hint=None):
        """Applies the information contained on the scope to the given event."""

        def _drop(event, cause, ty):
            logger.info("%s (%s) dropped event (%s)", ty, cause, event)

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

        exc_info = hint.get("exc_info") if hint is not None else None
        if exc_info is not None:
            for processor in self._error_processors:
                new_event = processor(event, exc_info)
                if new_event is None:
                    return _drop(event, processor, "error processor")
                event = new_event

        for processor in chain(global_event_processors, self._event_processors):
            new_event = event
            with capture_internal_exceptions():
                new_event = processor(event, hint)
            if new_event is None:
                return _drop(event, processor, "event processor")
            event = new_event

        return event

    def __copy__(self):
        rv = object.__new__(self.__class__)

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

        return rv

    def __repr__(self):
        return "<%s id=%s name=%s>" % (
            self.__class__.__name__,
            hex(id(self)),
            self._name,
        )
