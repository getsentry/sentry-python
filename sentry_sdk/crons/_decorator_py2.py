from functools import wraps

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable


class _MonitorMixin:
    def __call__(self, fn):
        # type: (Callable[..., Any]) -> Callable[..., Any]
        @wraps(fn)
        def inner(*args, **kwargs):
            # type: (Any, Any) -> Any
            with self:  # type: ignore[attr-defined]
                return fn(*args, **kwargs)

        return inner
