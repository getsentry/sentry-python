from functools import wraps
from inspect import iscoroutinefunction

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable


class _MonitorMixin:
    def __call__(self, fn):
        # type: (Callable[..., Any]) -> Callable[..., Any]
        if iscoroutinefunction(fn):

            @wraps(fn)
            async def inner(*args, **kwargs):
                with self:
                    return await fn(*args, **kwargs)

        else:

            @wraps(fn)
            def inner(*args, **kwargs):
                with self:
                    return fn(*args, **kwargs)

        return inner
