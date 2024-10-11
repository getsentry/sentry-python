import itertools


class FlagManager:
    """
    Right now this is just an interface for the buffer but it might contain
    thread-local state handling in the future.
    """

    def __init__(self, capacity):
        # type: (int) -> None
        self.buffer = FlagBuffer(capacity)

    def get_flags(self):
        # type: () -> list[dict]
        return self.buffer.serialize()

    def set_flag(self, flag, result):
        # type: (str, bool) -> None
        self.buffer.insert(flag, result)


class FlagBuffer:

    def __init__(self, capacity):
        # type: (int) -> None
        self.buffer = []  # type: list[Flag]
        self.capacity = capacity
        self.ip = 0

    @property
    def index(self):
        return self.ip % self.capacity

    def insert(self, flag, result):
        # type: (str, bool) -> None
        flag_ = Flag(flag, result)

        if self.ip >= self.capacity:
            self.buffer[self.index] = flag_
        else:
            self.buffer.append(flag_)

        self.ip += 1

    def serialize(self):
        # type: () -> list[dict]
        if self.ip >= self.capacity:
            iterator = itertools.chain(
                range(self.index, self.capacity), range(0, self.index)
            )
            return [self.buffer[i].asdict for i in iterator]
        else:
            return [flag.asdict for flag in self.buffer]


class Flag:
    __slots__ = ("flag", "result")

    def __init__(self, flag, result):
        # type: (str, bool) -> None
        self.flag = flag
        self.result = result

    @property
    def asdict(self):
        # type: () -> dict
        return {"flag": self.flag, "result": self.result}
