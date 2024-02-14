from __future__ import absolute_import

import sys
from functools import wraps
from threading import Thread, current_thread

import sentry_sdk
from sentry_sdk._compat import reraise
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import Scope
from sentry_sdk.utils import (
    event_from_exception,
    capture_internal_exceptions,
    logger,
)

if TYPE_CHECKING:
    from typing import Any
    from typing import TypeVar
    from typing import Callable
    from typing import Optional

    from sentry_sdk._types import ExcInfo

    F = TypeVar("F", bound=Callable[..., Any])


class ThreadingIntegration(Integration):
    identifier = "threading"

    def __init__(self, propagate_hub=None, propagate_scope=False):
        # type: (Optional[bool], bool) -> None
        if propagate_hub is not None:
            logger.warning(
                "Deprecated: propagate_hub is deprecated. Use propagate_scope instead. This will be removed in the future."
            )
        self.propagate_scope = propagate_scope

        # For backwards compatiblity when users set propagate_hub use this as propagate_scope.
        # Remove this when propagate_hub is removed.
        if propagate_hub is not None:
            self.propagate_scope = propagate_hub

    @staticmethod
    def setup_once():
        # type: () -> None
        old_start = Thread.start

        @wraps(old_start)
        def sentry_start(self, *a, **kw):
            # type: (Thread, *Any, **Any) -> Any
            integration = sentry_sdk.get_client().get_integration(ThreadingIntegration)
            if integration is not None:
                if integration.propagate_scope:
                    scope = sentry_sdk.get_isolation_scope()
                else:
                    scope = None

                # Patching instance methods in `start()` creates a reference cycle if
                # done in a naive way. See
                # https://github.com/getsentry/sentry-python/pull/434
                #
                # In threading module, using current_thread API will access current thread instance
                # without holding it to avoid a reference cycle in an easier way.
                with capture_internal_exceptions():
                    new_run = _wrap_run(scope, getattr(self.run, "__func__", self.run))
                    self.run = new_run  # type: ignore

            return old_start(self, *a, **kw)

        Thread.start = sentry_start  # type: ignore


def _wrap_run(scope, old_run_func):
    # type: (Optional[Scope], F) -> F
    @wraps(old_run_func)
    def run(*a, **kw):
        # type: (*Any, **Any) -> Any
        def _run_old_run_func():
            # type: () -> Any
            try:
                self = current_thread()
                return old_run_func(self, *a, **kw)
            except Exception:
                reraise(*_capture_exception())

        if scope is not None:
            with sentry_sdk.isolation_scope(scope):
                return _run_old_run_func()
        else:
            return _run_old_run_func()

    return run  # type: ignore


def _capture_exception():
    # type: () -> ExcInfo
    exc_info = sys.exc_info()

    client = sentry_sdk.get_client()
    if client.get_integration(ThreadingIntegration) is not None:
        event, hint = event_from_exception(
            exc_info,
            client_options=client.options,
            mechanism={"type": "threading", "handled": False},
        )
        sentry_sdk.capture_event(event, hint=hint)

    return exc_info
