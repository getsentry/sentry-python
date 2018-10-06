from __future__ import absolute_import

from dramatiq import Middleware, get_broker

from sentry_sdk import Hub
from sentry_sdk.integrations import Integration
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception


class DramatiqIntegration(Integration):
    """Automatically integrates the current Dramatiq broker with
    Sentry.  If you use this, then make sure you call sentry_sdk.init
    *after* setting up your broker, otherwise you can use the Sentry
     middleware in this module directly.
    """

    identifier = "dramatiq"

    def install(self):
        get_broker().add_middleware(Sentry())


class Sentry(Middleware):
    """A Dramatiq middleware that automatically captures and sends
    exceptions to Sentry.  This is automatically set up by
    DramatiqIntegration.
    """

    def before_process_message(self, broker, message):
        with capture_internal_exceptions():
            hub = Hub.current
            hub.push_scope()
            with hub.configure_scope() as scope:
                scope.transaction = message.actor_name

    def after_process_message(self, broker, message, *, result=None, exception=None):
        hub = Hub.current

        try:
            if exception is not None:
                event, hint = event_from_exception(
                    exception,
                    client_options=hub.client.options,
                    mechanism={"type": "dramatiq", "handled": False},
                )
                hub.capture_event(event, hint=hint)
        finally:
            hub.pop_scope_unsafe()
