from typing import TYPE_CHECKING
from sentry_sdk._lru_cache import LRUCache, KEY, NEXT, VALUE


if TYPE_CHECKING:
    from sentry_sdk._types import Flag as FType, Flags


class Flag:

    __slots__ = ("name", "result")

    def __init__(self, name, result):
        # type: (str, bool) -> None
        self.result = result
        self.name = name

    @property
    def as_dict(self):
        # type: () -> FType
        return {
            "flag": self.name,
            "result": self.result,
        }


class FlagManager:

    def __init__(self, capacity):
        # type: (int) -> None
        self._cache = LRUCache(max_size=capacity)

    def add(self, name, result):
        # type: (str, bool) -> None
        # NOTE: Should we log null names?
        if name is not None:
            self._cache.set(name, Flag(name, result))

    def serialize(self):
        # type: () -> Flags
        """Serialize flags.

        Flags are serialized in order of first-evaluation.
        """

        def iter_flags():
            # This only works if you know the root node is the only
            # item with a null key. The list is circularly linked so we
            # need a termination condition.
            node = self._cache.root[NEXT]
            while node[KEY] is not None:
                yield node[VALUE].as_dict
                node = node[NEXT]

        return [f for f in iter_flags()]
