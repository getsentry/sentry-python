from sentry_sdk.hub import Hub, init
from sentry_sdk.scope import Scope
from sentry_sdk.transport import Transport, HttpTransport
from sentry_sdk.client import Client

from sentry_sdk.api import *  # noqa

from sentry_sdk.consts import VERSION  # noqa

from sentry_sdk.crons import monitor  # noqa
from sentry_sdk.tracing import trace  # noqa

__all__ = [  # noqa
    "Hub",
    "Scope",
    "Client",
    "Transport",
    "HttpTransport",
    "init",
    "integrations",
    "trace",
    # From sentry_sdk.api
    "add_breadcrumb",
    "capture_event",
    "capture_exception",
    "capture_message",
    "configure_scope",
    "continue_trace",
    "flush",
    "get_baggage",
    "get_client",
    "get_current_span",
    "get_traceparent",
    "is_initialized",
    "isolation_scope",
    "last_event_id",
    "new_scope",
    "push_scope",
    "set_context",
    "set_extra",
    "set_level",
    "set_measurement",
    "set_tag",
    "set_tags",
    "set_user",
    "start_span",
    "start_transaction",
]

# Initialize the debug support after everything is loaded
from sentry_sdk.debug import init_debug_support

init_debug_support()
del init_debug_support
