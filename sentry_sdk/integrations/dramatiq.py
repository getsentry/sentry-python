import json

import sentry_sdk
from sentry_sdk.integrations import Integration
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.utils import (
    AnnotatedValue,
    capture_internal_exceptions,
    event_from_exception,
)

from dramatiq.broker import Broker  # type: ignore
from dramatiq.message import Message  # type: ignore
from dramatiq.middleware import Middleware, default_middleware  # type: ignore
from dramatiq.errors import Retry  # type: ignore

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, Optional, Union
    from sentry_sdk._types import Event, Hint


class DramatiqIntegration(Integration):
    """
    Dramatiq integration for Sentry

    Please make sure that you call `sentry_sdk.init` *before* initializing
    your broker, as it monkey patches `Broker.__init__`.
    """

    identifier = "dramatiq"

    @staticmethod
    def setup_once():
        # type: () -> None
        _patch_dramatiq_broker()


def _patch_dramatiq_broker():
    # type: () -> None
    original_broker__init__ = Broker.__init__

    def sentry_patched_broker__init__(self, *args, **kw):
        # type: (Broker, *Any, **Any) -> None
        integration = sentry_sdk.get_client().get_integration(DramatiqIntegration)

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
    """
    A Dramatiq middleware that automatically captures and sends
    exceptions to Sentry.

    This is automatically added to every instantiated broker via the
    DramatiqIntegration.
    """

    def before_process_message(self, broker, message):
        # type: (Broker, Message) -> None
        integration = sentry_sdk.get_client().get_integration(DramatiqIntegration)
        if integration is None:
            return

        message._scope_manager = sentry_sdk.new_scope()
        message._scope_manager.__enter__()

        scope = sentry_sdk.get_current_scope()
        scope.transaction = message.actor_name
        scope.set_tag("dramatiq_message_id", message.message_id)
        scope.add_event_processor(
            _make_message_event_processor(message, integration)
        )

    def after_process_message(self, broker, message, *, result=None, exception=None):
        # type: (Broker, Message, Any, Optional[Any], Optional[Exception]) -> None
        integration = sentry_sdk.get_client().get_integration(DramatiqIntegration)
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
                    client_options=sentry_sdk.get_client().options,
                    mechanism={
                        "type": DramatiqIntegration.identifier, 
                        "handled": False, 
                    },
                )
                sentry_sdk.capture_event(event, hint=hint)
        finally:
            message._scope_manager.__exit__(None, None, None)


def _make_message_event_processor(message, integration):
    # type: (Message, DramatiqIntegration) -> Callable[[Event, Hint], Optional[Event]]

    def inner(event, hint):
        # type: (Event, Hint) -> Optional[Event]
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
        # type: (Event) -> None
        client = sentry_sdk.get_client()
        if not client.is_active():
            return

        data = None  # type: Optional[Union[AnnotatedValue, Dict[str, Any]]]

        content_length = self.content_length()
        contexts = event.setdefault("contexts", {})
        request_info = contexts.setdefault("dramatiq", {})
        request_info["type"] = "dramatiq"

        max_request_body_size = client.options["max_request_body_size"]
        if (
            max_request_body_size == "never"
            or (max_request_body_size == "small" and content_length > 10**3)
            or (max_request_body_size == "medium" and content_length > 10**4)
        ):
            data = AnnotatedValue(
                "",
                {"rem": [["!config", "x", 0, content_length]], "len": content_length},
            )
        else:
            data = self.message_data

        request_info["data"] = data
