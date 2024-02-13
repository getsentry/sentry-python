import uuid
import warnings

from collections.abc import MutableMapping
from enum import Enum

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional, ParamSpec, TypeVar
    from collections.abc import Callable, Iterator

    P = ParamSpec("P")
    R = TypeVar("R")


def _warn_deprecated(extra_message=""):
    # type: (str) -> Callable[[Callable[P, R]], Callable[P, R]]
    def inner(func):
        # type: (Callable[P, R]) -> Callable[P, R]
        def wrapper(*args, **kwargs):
            # type: (...) -> R
            warnings.warn(
                f"Event.{func.__name__} is deprecated and may be removed in a future major release. {extra_message}",
                DeprecationWarning,
                stacklevel=2,
            )
            return func(*args, **kwargs)

        return wrapper

    return inner


class Event(MutableMapping):
    """
    Conditionally type-safe wrapper around a dictionary that represents an event. To maintain backwards compatibility,
    this class can be used as if it were a dictionary; however, using as a dictionary is deprecated and may be removed
    in a future major release.

    Type-safety is guaranteed unless and until one of the following happens:
      - The Event is constructed with a non-None value for the `data` parameter.
      - The `__setitem__` method is called.
      - The `__delitem__` method is called.

    If any of the above happen, a warning will be issued to advise users that type safety can no longer be guaranteed.
    This warning is issued in addition to a deprecation warning, since all of the above are also deprecated.
    """

    PLATFORM = "python"

    class Level(Enum):
        FATAL = "fatal"
        ERROR = "error"
        WARNING = "warning"
        INFO = "info"
        DEBUG = "debug"

    def __init__(
        self,
        data=None,  # type: Optional[dict[str, object]]
        exceptions=None,  # type: Optional[list[dict[str, Any]]]
        level=None,  # type: Optional[Event.Level]
    ):
        # type: (...) -> None
        self._data = {}  # type: dict[str, Any]
        if data is not None:
            warnings.warn(
                "Passing non-None data to Event.__init__ is deprecated.",
                DeprecationWarning,
            )
            self._warn_type_safety()
            self._data.update(data)

        # TODO: Maybe add timestamp here?
        self._data.update(
            {
                "event_id": uuid.uuid4(),
                "platform": self.PLATFORM,
            }
        )

        if exceptions is not None:
            self._data["exception"] = {"values": exceptions}

        if level is not None:
            self._data["level"] = level.value

    def get_exceptions(self):
        # type: () -> list[dict[str, Any]]
        """
        Get the list of exceptions that are part of this event.
        """
        return self._data.get("exception", {}).get("values", [])

    # def exceptions(self, exception_values):
    #     # type: (list[dict[str, Any]]) -> Self
    #     """
    #     Set the exception values for this event, and return the event.
    #     """
    #     self._data["exception"] = {"values": exception_values}
    #     return self

    # def level(self, level):
    #     # type: (Event.Level) -> Self
    #     """
    #     Set the level of this event, returning the event.
    #     """
    #     self._data["level"] = level.value
    #     return self

    @_warn_deprecated("Please use the getter methods, instead.")
    def __getitem__(self, key):
        # type: (str) -> object
        return self._data[key]

    @_warn_deprecated("Please use the setter methods, instead.")
    def __setitem__(self, key, value):
        # type: (str, object) -> None
        self._warn_type_safety()
        self._data[key] = value

    @_warn_deprecated()
    def __delitem__(self, key):
        # type: (str) -> None
        self._warn_type_safety()
        del self._data[key]

    @_warn_deprecated()
    def __iter__(self):
        # type: () -> Iterator[str]
        return iter(self._data)

    @_warn_deprecated()
    def __len__(self):
        # type: () -> int
        return len(self._data)

    def __str__(self):
        # type: () -> str
        return str(self._data)

    def __repr__(self):
        # type: () -> str
        return f"<Event {repr(self._data)}>"

    def _warn_type_safety(self):
        warnings.warn(
            f"Type safety of the Event {repr(self)} can no longer be guaranteed!"
        )
