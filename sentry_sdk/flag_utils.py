from copy import copy
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk._lru_cache import LRUCache

if TYPE_CHECKING:
    from typing import TypedDict, Optional
    from sentry_sdk._types import Event, ExcInfo

    FlagData = TypedDict("FlagData", {"flag": str, "result": bool})


DEFAULT_FLAG_CAPACITY = 100


class FlagBuffer:

    def __init__(self, capacity):
        # type: (int) -> None
        self.buffer = LRUCache(capacity)
        self.capacity = capacity

    def clear(self):
        # type: () -> None
        self.buffer = LRUCache(self.capacity)

    def __copy__(self):
        # type: () -> FlagBuffer
        buffer = FlagBuffer(capacity=self.capacity)
        buffer.buffer = copy(self.buffer)
        return buffer

    def get(self):
        # type: () -> list[FlagData]
        return [{"flag": key, "result": value} for key, value in self.buffer.get_all()]

    def set(self, flag, result):
        # type: (str, bool) -> None
        self.buffer.set(flag, result)


def flag_error_processor(event, exc_info):
    # type: (Event, ExcInfo) -> Optional[Event]
    scope = sentry_sdk.get_current_scope()
    event["contexts"]["flags"] = {"values": scope.flags.get()}
    return event
