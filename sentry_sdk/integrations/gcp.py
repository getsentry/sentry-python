import sys

from sentry_sdk.hub import Hub
from sentry_sdk._compat import reraise
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
)
from sentry_sdk.integrations import Integration

from sentry_sdk._types import MYPY


if MYPY:
    from typing import Any
    from typing import TypeVar
    from typing import Callable

    F = TypeVar("F", bound=Callable[..., Any])


def _wrap_func(func):
    # type: (F) -> F
    def sentry_func(*args, **kwargs):
        # type: (*Any, **Any) -> Any

        hub = Hub.current
        integration = hub.get_integration(GcpIntegration)
        if integration is None:
            return func(*args, **kwargs)

        # If an integration is there, a client has to be there.
        client = hub.client  # type: Any

        with hub.push_scope() as scope:
            with capture_internal_exceptions():
                scope.clear_breadcrumbs()
            try:
                return func(*args, **kwargs)
            except Exception:
                exc_info = sys.exc_info()
                event, hint = event_from_exception(
                    exc_info,
                    client_options=client.options,
                    mechanism={"type": "gcp", "handled": False},
                )
                hub.capture_event(event, hint=hint)
                reraise(*exc_info)
            finally:
                # Flush out the event queue
                hub.flush()

    return sentry_func  # type: ignore


class GcpIntegration(Integration):
    identifier = "gcp"

    @staticmethod
    def setup_once():
        # type: () -> None
        import __main__ as gcp_functions  # type: ignore

        worker1 = gcp_functions.worker_v1

        worker1.FunctionHandler.invoke_user_function = _wrap_func(
            worker1.FunctionHandler.invoke_user_function
        )
