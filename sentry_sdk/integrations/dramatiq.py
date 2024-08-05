import json
from typing import Any, Callable, Dict, Optional, Union

from dramatiq.broker import Broker
from dramatiq.message import Message
from dramatiq.middleware import Middleware, default_middleware
from dramatiq.errors import Retry
from sentry_sdk import Hub
from sentry_sdk.integrations import Integration
from sentry_sdk.utils import (
    AnnotatedValue,
    capture_internal_exceptions,
    event_from_exception,
)


class DramatiqIntegration(Integration):
    """Dramatiq integration for Sentry

    Please make sure that you call `sentry_sdk.init` *before* initializing
    your broker, as it monkey patches `Broker.__init__`.
    """

    identifier = "dramatiq"

    @staticmethod
    def setup_once():
        # type: () -> None
        _patch_dramatiq_broker()


def _patch_dramatiq_broker():
    original_broker__init__ = Broker.__init__

    def sentry_patched_broker__init__(self, *args, **kw):
        hub = Hub.current
        integration = hub.get_integration(DramatiqIntegration)

        try:
            middleware = kw.pop("middleware")
        except KeyError:
            # Unfortunately Broker and StubBroker allows middleware to be
            # passed in as positional arguments, whilst RabbitmqBroker and
            # RedisBroker does not.
            if len(args) > 0:
                assert len(args) < 2
                middleware = None if args[0] is None else args[0]
                args = []
            else:
                middleware = None

        if middleware is None:
            middleware = list(m() for m in default_middleware)
        else:
            middleware = list(middleware)

        if integration is not None:
            assert SentryMiddleware not in (
                m.__class__ for m in middleware
            ), "Sentry middleware must not be passed in manually to broker"
            middleware.insert(0, SentryMiddleware())

        kw["middleware"] = middleware
        # raise Exception([args, kw])
        original_broker__init__(self, *args, **kw)

    Broker.__init__ = sentry_patched_broker__init__


class SentryMiddleware(Middleware):
    """A Dramatiq middleware that automatically captures and sends
    exceptions to Sentry.

    This is automatically added to every instantiated broker via the
    DramatiqIntegration.
    """

    def before_process_message(self, broker, message):
        hub = Hub.current
        integration = hub.get_integration(DramatiqIntegration)
        if integration is None:
            return

        message._scope_manager = hub.push_scope()
        message._scope_manager.__enter__()

        with hub.configure_scope() as scope:
            scope.transaction = message.actor_name
            scope.set_tag("dramatiq_message_id", message.message_id)
            scope.add_event_processor(
                _make_message_event_processor(message, integration)
            )

    def after_process_message(self, broker, message, *, result=None, exception=None):
        hub = Hub.current
        integration = hub.get_integration(DramatiqIntegration)

        if integration is None:
            return

        actor = broker.get_actor(message.actor_name)
        throws = message.options.get("throws") or actor.options.get("throws")

        try:
            if (
                exception is not None
                and not (throws and isinstance(exception, throws))
                and not isinstance(exception, Retry)
            ):
                event, hint = event_from_exception(
                    exception,
                    client_options=hub.client.options,
                    mechanism={"type": "dramatiq", "handled": False},
                )
                hub.capture_event(event, hint=hint)
        finally:
            message._scope_manager.__exit__(None, None, None)


def _make_message_event_processor(message, integration):
    # type: (Message, DramatiqIntegration) -> Callable

    def inner(event, hint):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        with capture_internal_exceptions():
            DramatiqMessageExtractor(message).extract_into_event(event)

        return event

    return inner


class DramatiqMessageExtractor(object):
    def __init__(self, message):
        # type: (Message) -> None
        self.message_data = dict(message.asdict())

    def content_length(self):
        # type: () -> int
        return len(json.dumps(self.message_data))

    def extract_into_event(self, event):
        # type: (Dict[str, Any]) -> None
        client = Hub.current.client
        if client is None:
            return

        data = None  # type: Optional[Union[AnnotatedValue, Dict[str, Any]]]

        content_length = self.content_length()
        contexts = event.setdefault("contexts", {})
        request_info = contexts.setdefault("dramatiq", {})
        request_info["type"] = "dramatiq"

        bodies = client.options["request_bodies"]
        if (
            bodies == "never"
            or (bodies == "small" and content_length > 10**3)
            or (bodies == "medium" and content_length > 10**4)
        ):
            data = AnnotatedValue(
                "",
                {"rem": [["!config", "x", 0, content_length]], "len": content_length},
            )
        else:
            data = self.message_data

        request_info["data"] = data
