from .utils import logger


class Scope(object):
    """The scope holds extra information that should be sent with all
    events that belong to it.
    """

    __slots__ = ["_data", "_breadcrumbs", "_event_processors", "_error_processors"]

    def __init__(self):
        self._data = {}
        self._breadcrumbs = []
        self._event_processors = []
        self._error_processors = []

    def _set_fingerprint(self, value):
        """When set this overrides the default fingerprint."""
        self._data["fingerprint"] = value

    fingerprint = property(fset=_set_fingerprint, doc=_set_fingerprint.__doc__)
    del _set_fingerprint

    def _set_transaction(self, value):
        """When set this forces a specific transaction name to be set."""
        self._data["transaction"] = value

    transaction = property(fset=_set_transaction, doc=_set_transaction.__doc__)
    del _set_transaction

    def _set_user(self, value):
        """When set a specific user is bound to the scope."""
        self._data["user"] = value

    user = property(fset=_set_user, doc=_set_user.__doc__)
    del _set_user

    def set_tag(self, key, value):
        """Sets a tag for a key to a specific value."""
        self._data.setdefault("tags", {})[key] = value

    def remove_tag(self, key):
        """Removes a specific tag."""
        self._data.setdefault("tags", {}).pop(key, None)

    def set_context(self, key, value):
        """Binds a context at a certain key to a specific value."""
        self._data.setdefault("contexts", {})[key] = value

    def remove_context(self, key):
        """Removes a context."""
        self._data.setdefault("contexts", {}).pop(key, None)

    def set_extra(self, key, value):
        """Sets an extra key to a specific value."""
        self._data.setdefault("extras", {})[key] = value

    def remove_extra(self, key):
        """Removes a specific extra key."""
        self._data.setdefault("extras", {}).pop(key, None)

    def clear(self):
        """Clears the entire scope."""
        self._data.clear()
        del self._breadcrumbs[:]
        del self._event_processors[:]

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

        event.setdefault("breadcrumbs", []).extend(self._breadcrumbs)
        if event.get("user") is None and "user" in self._data:
            event["user"] = self._data["user"]

        if event.get("transaction") is None and "transaction" in self._data:
            event["transaction"] = self._data["transaction"]

        extra = self._data.get("extra")
        if extra:
            event.setdefault("extra", {}).update(extra)

        tags = self._data.get("tags")
        if tags:
            event.setdefault("tags", {}).update(tags)

        contexts = self._data.get("contexts")
        if contexts:
            event.setdefault("contexts", {}).update(contexts)

        if hint is not None and hint.exc_info is not None:
            exc_info = hint.exc_info
            for processor in self._error_processors:
                new_event = processor(event, exc_info)
                if new_event is None:
                    return _drop(event, processor, "error processor")
                event = new_event

        for processor in self._event_processors:
            new_event = processor(event)
            if new_event is None:
                return _drop(event, processor, "event processor")
            event = new_event

        return event

    def __copy__(self):
        rv = object.__new__(self.__class__)
        rv._data = dict(self._data)
        rv._breadcrumbs = list(self._breadcrumbs)
        rv._event_processors = list(self._event_processors)
        rv._error_processors = list(self._error_processors)
        return rv
