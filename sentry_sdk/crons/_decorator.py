from functools import wraps
from inspect import iscoroutinefunction

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import (
        Awaitable,
        Callable,
        ParamSpec,
        TypeVar,
        Union,
    )

    P = ParamSpec("P")
    R = TypeVar("R")


class MonitorMixin:
    def __call__(self, fn):
        # type: (Callable[P, R]) -> Callable[P, Union[R, Awaitable[R]]]
        if iscoroutinefunction(fn):

            @wraps(fn)
            async def inner(*args: "P.args", **kwargs: "P.kwargs"):
                # type: (...) -> R
                with self:  # type: ignore[attr-defined]
                    return await fn(*args, **kwargs)

        else:

            @wraps(fn)
            def inner(*args: "P.args", **kwargs: "P.kwargs"):
                # type: (...) -> R
                with self:  # type: ignore[attr-defined]
                    return fn(*args, **kwargs)

        return inner
