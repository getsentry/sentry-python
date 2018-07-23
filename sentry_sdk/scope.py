class Scope(object):
    __slots__ = ["_data", "_breadcrumbs", "_event_processors"]

    def __init__(self):
        self._data = {}
        self._breadcrumbs = []
        self._event_processors = []

    def _set_fingerprint(self, value):
        self._data["fingerprint"] = value

    fingerprint = property(fset=_set_fingerprint)
    del _set_fingerprint

    def _set_transaction(self, value):
        self._data["transaction"] = value

    transaction = property(fset=_set_transaction)
    del _set_transaction

    def _set_user(self, value):
        self._data["user"] = value

    user = property(fset=_set_user)
    del _set_user

    def _set_request(self, request):
        self._data["request"] = request

    request = property(fset=_set_request)
    del _set_request

    def set_tag(self, key, value):
        self._data.setdefault("tags", {})[key] = value

    def remove_tag(self, key):
        self._data.setdefault("tags", {}).pop(key, None)

    def set_context(self, key, value):
        self._data.setdefault("contexts", {})[key] = value

    def remove_context(self, key):
        self._data.setdefault("contexts", {}).pop(key, None)

    def set_extra(self, key, value):
        self._data.setdefault("extras", {})[key] = value

    def remove_extra(self, key):
        self._data.setdefault("extras", {}).pop(key, None)

    def clear(self):
        self._data.clear()
        del self._breadcrumbs[:]
        del self._event_processors[:]

    def apply_to_event(self, event):
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

        tags = self._data.get("request")
        if tags:
            event.setdefault("request", {}).update(tags)

        contexts = self._data.get("contexts")
        if contexts:
            event.setdefault("contexts", {}).update(contexts)

        for processor in self._event_processors:
            processor(event)

    def __copy__(self):
        rv = object.__new__(self.__class__)
        rv._data = dict(self._data)
        rv._breadcrumbs = list(self._breadcrumbs)
        rv._event_processors = list(self._event_processors)
        return rv
