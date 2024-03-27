from functools import wraps
from inspect import iscoroutinefunction

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, ParamSpec, TypeVar

    P = ParamSpec("P")
    R = TypeVar("R")


class MonitorMixin:
    def __call__(self, fn):
        # type: (Callable[P, R]) -> Callable[P, R]
        if iscoroutinefunction(fn):

            @wraps(fn)
            async def inner(*args, **kwargs):
                # type: (Any, Any) -> Any
                with self:  # type: ignore[attr-defined]
                    return await fn(*args, **kwargs)

        else:

            @wraps(fn)
            def inner(*args, **kwargs):
                # type: (Any, Any) -> Any
                with self:  # type: ignore[attr-defined]
                    return fn(*args, **kwargs)

        return inner
