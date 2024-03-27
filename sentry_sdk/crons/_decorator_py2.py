from functools import wraps

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable


class _MonitorMixin:
    def __call__(self, fn):
        # type: (Callable[..., Any]) -> Callable[..., Any]
        @wraps(fn)
        def inner(*args, **kwargs):
            with self:
                return fn(*args, **kwargs)

        return inner
