from copy import copy
from typing import TYPE_CHECKING

import itertools

if TYPE_CHECKING:
    from typing import TypedDict

    FlagData = TypedDict("FlagData", {"flag": str, "result": bool})


class FlagBuffer:

    def __init__(self, capacity):
        # type: (int) -> None
        self.buffer = []  # type: list[Flag]
        self.capacity = capacity
        self.ip = 0

    @property
    def index(self):
        # type: () -> int
        return self.ip % self.capacity

    def clear(self):
        # type: () -> None
        self.buffer = []
        self.ip = 0

    def copy(self):
        # type: () -> FlagBuffer
        buffer = FlagBuffer(capacity=self.capacity)
        buffer.buffer = copy(self.buffer)
        buffer.ip = self.ip
        return buffer

    def get(self):
        # type: () -> list[FlagData]
        if self.ip >= self.capacity:
            iterator = itertools.chain(
                range(self.index, self.capacity), range(0, self.index)
            )
            return [self.buffer[i].asdict for i in iterator]
        else:
            return [flag.asdict for flag in self.buffer]

    def set(self, flag, result):
        # type: (str, bool) -> None
        flag_ = Flag(flag, result)

        if self.ip >= self.capacity:
            self.buffer[self.index] = flag_
        else:
            self.buffer.append(flag_)

        self.ip += 1


class Flag:
    __slots__ = ("flag", "result")

    def __init__(self, flag, result):
        # type: (str, bool) -> None
        self.flag = flag
        self.result = result

    @property
    def asdict(self):
        # type: () -> FlagData
        return {"flag": self.flag, "result": self.result}
