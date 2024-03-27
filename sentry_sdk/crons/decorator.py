from functools import wraps

try:
    from inspect import iscoroutinefunction
except ImportError:
    iscoroutinefunction = lambda f: False

from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.crons import capture_checkin
from sentry_sdk.crons.consts import MonitorStatus
from sentry_sdk.utils import now


if TYPE_CHECKING:
    from typing import Callable, Optional, Type
    from types import TracebackType


class monitor:
    def __init__(self, monitor_slug=None):
        # type: (str) -> None
        self.monitor_slug = monitor_slug

    def __enter__(self):
        # type: () -> None
        self.start_timestamp = now()
        self.check_in_id = capture_checkin(
            monitor_slug=self.monitor_slug, status=MonitorStatus.IN_PROGRESS
        )

    def __exit__(self, exc_type, exc_value, traceback):
        # type: (Optional[Type[BaseException]], Optional[BaseException], Optional[TracebackType]) -> None
        duration_s = now() - self.start_timestamp

        if exc_type is None and exc_value is None and traceback is None:
            status = MonitorStatus.OK
        else:
            status = MonitorStatus.ERROR

        capture_checkin(
            monitor_slug=self.monitor_slug,
            check_in_id=self.check_in_id,
            status=status,
            duration=duration_s,
        )

    def __call__(self, fn):
        # type: (Callable) -> Callable
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
