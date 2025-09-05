from functools import wraps

from concurrent.futures import ThreadPoolExecutor

import sentry_sdk
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import use_isolation_scope, use_scope

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from typing import Callable


class ConcurrentIntegration(Integration):
    identifier = "concurrent"

    def __init__(self, record_exceptions_on_futures=True):
        # type: (bool) -> None
        self.record_exceptions_on_futures = record_exceptions_on_futures

    @staticmethod
    def setup_once():
        # type: () -> None
        old_submit = ThreadPoolExecutor.submit

        @wraps(old_submit)
        def sentry_submit(self, fn, *args, **kwargs):
            # type: (ThreadPoolExecutor, Callable, *Any, **Any) -> Any
            integration = sentry_sdk.get_client().get_integration(ConcurrentIntegration)
            if integration is None:
                return old_submit(self, fn, *args, **kwargs)

            isolation_scope = sentry_sdk.get_isolation_scope().fork()
            current_scope = sentry_sdk.get_current_scope().fork()

            def wrapped_fn(*args, **kwargs):
                # type: (*Any, **Any) -> Any
                with use_isolation_scope(isolation_scope):
                    with use_scope(current_scope):
                        return fn(*args, **kwargs)

            return old_submit(self, wrapped_fn, *args, **kwargs)

        ThreadPoolExecutor.submit = sentry_submit
