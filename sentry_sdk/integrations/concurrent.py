from functools import wraps

from concurrent.futures import ThreadPoolExecutor, Future

import sentry_sdk
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import use_isolation_scope, use_scope
from sentry_sdk.utils import event_from_exception

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, TypeVar

    T = TypeVar("T", bound=Any)


class ConcurrentIntegration(Integration):
    identifier = "concurrent"

    def __init__(self, record_exceptions_on_futures=True):
        # type: (bool) -> None
        self.record_exceptions_on_futures = record_exceptions_on_futures

    @staticmethod
    def setup_once():
        # type: () -> None
        ThreadPoolExecutor.submit = _wrap_submit_call(ThreadPoolExecutor.submit)  # type: ignore


def _wrap_submit_call(func):
    # type: (Callable[..., Future[Any]]) -> Callable[..., Future[Any]]
    """
    Wrap submit call to propagate scopes on task submission.
    """

    @wraps(func)
    def sentry_submit(self, fn, *args, **kwargs):
        # type: (ThreadPoolExecutor, Callable[..., T], *Any, **Any) -> Future[T]
        integration = sentry_sdk.get_client().get_integration(ConcurrentIntegration)
        if integration is None:
            return func(self, fn, *args, **kwargs)

        isolation_scope = sentry_sdk.get_isolation_scope().fork()
        current_scope = sentry_sdk.get_current_scope().fork()

        def wrapped_fn(*args, **kwargs):
            # type: (*Any, **Any) -> Any
            with use_isolation_scope(isolation_scope):
                with use_scope(current_scope):
                    return fn(*args, **kwargs)

        future = func(self, wrapped_fn, *args, **kwargs)

        def report_exceptions(future):
            # type: (Future[Any]) -> None
            exception = future.exception()
            integration = sentry_sdk.get_client().get_integration(ConcurrentIntegration)

            if (
                exception is None
                or integration is None
                or not integration.record_exceptions_on_futures
            ):
                return

            event, hint = event_from_exception(
                exception,
                client_options=sentry_sdk.get_client().options,
                mechanism={"type": "concurrent", "handled": False},
            )
            sentry_sdk.capture_event(event, hint=hint)

        if integration.record_exceptions_on_futures:
            future.add_done_callback(report_exceptions)

        return future

    return sentry_submit
