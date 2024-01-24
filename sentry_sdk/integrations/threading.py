from __future__ import absolute_import

import sys
from functools import wraps
from threading import Thread, current_thread

from sentry_sdk import Hub
from sentry_sdk._compat import reraise
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.integrations import Integration
from sentry_sdk.utils import (
    event_from_exception,
    capture_internal_exceptions,
    logger,
    copy_context,
)

if TYPE_CHECKING:
    from typing import Any
    from typing import TypeVar
    from typing import Callable
    from typing import Optional
    from contextvars import Context

    from sentry_sdk._types import ExcInfo

    F = TypeVar("F", bound=Callable[..., Any])


class ThreadingIntegration(Integration):
    identifier = "threading"

    def __init__(self, propagate_hub=False, propagate_context=False):
        # type: (bool, bool) -> None
        if propagate_hub:
            logger.warning(
                "Deprecated: propagate_hub is deprecated. Use propagate_context instead. This will be removed in the future."
            )
        self.propagate_context = propagate_context

        # Make propagate_hub an alias for propagate_context.
        # Remove this when propagate_hub is removed.
        if propagate_hub:
            self.propagate_context = propagate_hub

    @staticmethod
    def setup_once():
        # type: () -> None
        old_start = Thread.start

        @wraps(old_start)
        def sentry_start(self, *a, **kw):
            # type: (Thread, *Any, **Any) -> Any
            hub = Hub.current
            integration = hub.get_integration(ThreadingIntegration)
            if integration is not None:
                if not integration.propagate_context:
                    ctx = None
                else:
                    ctx = copy_context()

                # Patching instance methods in `start()` creates a reference cycle if
                # done in a naive way. See
                # https://github.com/getsentry/sentry-python/pull/434
                #
                # In threading module, using current_thread API will access current thread instance
                # without holding it to avoid a reference cycle in an easier way.
                with capture_internal_exceptions():
                    new_run = _wrap_run(ctx, getattr(self.run, "__func__", self.run))
                    self.run = new_run  # type: ignore

            return old_start(self, *a, **kw)

        Thread.start = sentry_start  # type: ignore


def _wrap_run(parent_context, old_run_func):
    # type: (Optional[Context], F) -> F
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

        if parent_context is not None:
            return parent_context.run(_run_old_run_func)
        else:
            return _run_old_run_func()

    return run  # type: ignore


def _capture_exception():
    # type: () -> ExcInfo
    hub = Hub.current
    exc_info = sys.exc_info()

    if hub.get_integration(ThreadingIntegration) is not None:
        # If an integration is there, a client has to be there.
        client = hub.client  # type: Any

        event, hint = event_from_exception(
            exc_info,
            client_options=client.options,
            mechanism={"type": "threading", "handled": False},
        )
        hub.capture_event(event, hint=hint)

    return exc_info
