import itertools
import time
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from sentry_sdk._types import Flag as FType, Flags


class Flag:

    __slots__ = ("call_count", "last_call_time", "name", "last_result")

    def __init__(self, name, result):
        # type: (str, bool) -> None
        self.call_count = 1
        self.last_call_time = time.time()
        self.last_result = result
        self.name = name

    @property
    def as_dict(self):
        # type: () -> FType
        return {
            "flag": self.name,
            "result": self.last_result,
        }

    def called(self, result):
        # type: (bool) -> None
        self.call_count += 1
        self.last_call_time = time.time()
        self.last_result = result
        return None


class FlagManager:

    def __init__(self, capacity):
        # type: (int) -> None
        self._buffer = []  # type: list[Flag]
        self._capacity = capacity
        self._flag_idx_map = {}  # type: dict[str, int]
        self._pointer = 0

    @property
    def index(self):
        return self._pointer % self._capacity

    def add(self, name, result):
        # type: (str, bool) -> None
        if name in self._flag_idx_map:
            # If the flag exists in the set record that the flag was
            # called again. Repeated calls to a flag does not alter its
            # ordering in the buffer. This is not an LRU cache. It is a
            # FIFO queue.
            self._buffer[self._flag_idx_map[name]].called(result)
            return None

        flag = Flag(name, result)

        if self._pointer >= self._capacity:
            # The old flag is fetched and removed from the mapping.
            old = self._buffer[self.index]
            self._flag_idx_map.pop(old.name)

            # The new flag overwrites the old flag in the buffer.
            self._buffer[self.index] = flag
        else:
            # Because the slots have not been populated yet we append
            # to the buffer until it fills up.
            self._buffer.append(flag)

        # The flag's name is added to the map with a copy of its index
        # position. Subsequent calls to this flag will be de-duplicated
        # unless the flag is removed from the buffer. In which case a
        # new entry for the flag will be created.
        self._flag_idx_map[flag.name] = self.index

        # The pointer always points to the next slot written to.
        self._pointer += 1

    def serialize(self):
        # type: () -> Flags
        """Serialize flags.

        Flags are serialized in order of first-evaluation.
        """
        if self._pointer >= self._capacity:
            # We've wrapped around the list and now our evaluation results
            # are unordered. Starting from the pointer (which always
            # points to the next slot) iterate to the end of the list.
            # Then starting from 0 iterate to the pointer.
            iterator = itertools.chain(
                range(self.index, self._capacity), range(0, self.index)
            )
            return [self._buffer[i].as_dict for i in iterator]
        else:
            # The buffer has not been filled. We can iterate up to the
            # index position (which always points to the next slot).
            return [self._buffer[i].as_dict for i in range(0, self.index)]
