import os
import uuid
import random
from datetime import datetime

from sentry_sdk._compat import string_types, text_type, iteritems
from sentry_sdk.utils import (
    handle_in_app,
    get_type_name,
    capture_internal_exceptions,
    current_stacktrace,
    logger,
)
from sentry_sdk.serializer import Serializer
from sentry_sdk.transport import make_transport
from sentry_sdk.consts import DEFAULT_OPTIONS, SDK_INFO, ClientConstructor
from sentry_sdk.integrations import setup_integrations
from sentry_sdk.utils import ContextVar

MYPY = False
if MYPY:
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import Optional

    from sentry_sdk.scope import Scope
    from sentry_sdk.utils import Event, Hint


_client_init_debug = ContextVar("client_init_debug")


def _get_options(*args, **kwargs):
    # type: (*Optional[str], **Any) -> Dict[str, Any]
    if args and (isinstance(args[0], (text_type, bytes, str)) or args[0] is None):
        dsn = args[0]  # type: Optional[str]
        args = args[1:]
    else:
        dsn = None

    rv = dict(DEFAULT_OPTIONS)
    options = dict(*args, **kwargs)  # type: ignore
    if dsn is not None and options.get("dsn") is None:
        options["dsn"] = dsn  # type: ignore

    for key, value in iteritems(options):
        if key not in rv:
            raise TypeError("Unknown option %r" % (key,))
        rv[key] = value

    if rv["dsn"] is None:
        rv["dsn"] = os.environ.get("SENTRY_DSN")

    if rv["release"] is None:
        rv["release"] = os.environ.get("SENTRY_RELEASE")

    if rv["environment"] is None:
        rv["environment"] = os.environ.get("SENTRY_ENVIRONMENT")

    return rv  # type: ignore


class _Client(object):
    """The client is internally responsible for capturing the events and
    forwarding them to sentry through the configured transport.  It takes
    the client options as keyword arguments and optionally the DSN as first
    argument.
    """

    def __init__(self, *args, **kwargs):
        # type: (*Optional[str], **Any) -> None
        old_debug = _client_init_debug.get(False)
        try:
            self.options = options = get_options(*args, **kwargs)  # type: ignore
            _client_init_debug.set(options["debug"])
            self.transport = make_transport(options)

            request_bodies = ("always", "never", "small", "medium")
            if options["request_bodies"] not in request_bodies:
                raise ValueError(
                    "Invalid value for request_bodies. Must be one of {}".format(
                        request_bodies
                    )
                )

            self.integrations = setup_integrations(
                options["integrations"], with_defaults=options["default_integrations"]
            )
        finally:
            _client_init_debug.set(old_debug)

    @property
    def dsn(self):
        # type: () -> Optional[str]
        """Returns the configured DSN as string."""
        return self.options["dsn"]

    def _prepare_event(
        self,
        event,  # type: Event
        hint,  # type: Optional[Hint]
        scope,  # type: Optional[Scope]
    ):
        # type: (...) -> Optional[Event]
        if event.get("timestamp") is None:
            event["timestamp"] = datetime.utcnow()

        hint = dict(hint or ())  # type: Hint

        if scope is not None:
            event_ = scope.apply_to_event(event, hint)
            if event_ is None:
                return None
            event = event_

        if (
            self.options["attach_stacktrace"]
            and "exception" not in event
            and "stacktrace" not in event
            and "threads" not in event
        ):
            with capture_internal_exceptions():
                event["threads"] = {
                    "values": [
                        {
                            "stacktrace": current_stacktrace(
                                self.options["with_locals"]
                            ),
                            "crashed": False,
                            "current": True,
                        }
                    ]
                }

        for key in "release", "environment", "server_name", "dist":
            if event.get(key) is None and self.options[key] is not None:  # type: ignore
                event[key] = text_type(self.options[key]).strip()  # type: ignore
        if event.get("sdk") is None:
            sdk_info = dict(SDK_INFO)
            sdk_info["integrations"] = sorted(self.integrations.keys())
            event["sdk"] = sdk_info

        if event.get("platform") is None:
            event["platform"] = "python"

        event = handle_in_app(
            event, self.options["in_app_exclude"], self.options["in_app_include"]
        )

        # Postprocess the event here so that annotated types do
        # generally not surface in before_send
        if event is not None:
            event = Serializer().serialize_event(event)

        before_send = self.options["before_send"]
        if before_send is not None:
            new_event = None
            with capture_internal_exceptions():
                new_event = before_send(event, hint or {})
            if new_event is None:
                logger.info("before send dropped event (%s)", event)
            event = new_event  # type: ignore

        return event

    def _is_ignored_error(self, event, hint):
        # type: (Event, Hint) -> bool
        exc_info = hint.get("exc_info")
        if exc_info is None:
            return False

        type_name = get_type_name(exc_info[0])
        full_name = "%s.%s" % (exc_info[0].__module__, type_name)

        for errcls in self.options["ignore_errors"]:
            # String types are matched against the type name in the
            # exception only
            if isinstance(errcls, string_types):
                if errcls == full_name or errcls == type_name:
                    return True
            else:
                if issubclass(exc_info[0], errcls):  # type: ignore
                    return True

        return False

    def _should_capture(
        self,
        event,  # type: Event
        hint,  # type: Hint
        scope=None,  # type: Optional[Scope]
    ):
        # type: (...) -> bool
        if scope is not None and not scope._should_capture:
            return False

        if (
            self.options["sample_rate"] < 1.0
            and random.random() >= self.options["sample_rate"]
        ):
            return False

        if self._is_ignored_error(event, hint):
            return False

        return True

    def capture_event(self, event, hint=None, scope=None):
        # type: (Dict[str, Any], Optional[Any], Optional[Scope]) -> Optional[str]
        """Captures an event.

        This takes the ready made event and an optional hint and scope.  The
        hint is internally used to further customize the representation of the
        error.  When provided it's a dictionary of optional information such
        as exception info.

        If the transport is not set nothing happens, otherwise the return
        value of this function will be the ID of the captured event.
        """
        if self.transport is None:
            return None
        if hint is None:
            hint = {}
        rv = event.get("event_id")
        if rv is None:
            event["event_id"] = rv = uuid.uuid4().hex
        if not self._should_capture(event, hint, scope):
            return None
        event = self._prepare_event(event, hint, scope)
        if event is None:
            return None
        self.transport.capture_event(event)
        return rv

    def close(self, timeout=None, callback=None):
        # type: (Optional[float], Optional[Callable[[int, float], None]]) -> None
        """
        Close the client and shut down the transport. Arguments have the same
        semantics as `self.flush()`.
        """
        if self.transport is not None:
            self.flush(timeout=timeout, callback=callback)
            self.transport.kill()
            self.transport = None

    def flush(self, timeout=None, callback=None):
        # type: (Optional[float], Optional[Callable[[int, float], None]]) -> None
        """
        Wait `timeout` seconds for the current events to be sent. If no
        `timeout` is provided, the `shutdown_timeout` option value is used.

        The `callback` is invoked with two arguments: the number of pending
        events and the configured timeout.  For instance the default atexit
        integration will use this to render out a message on stderr.
        """
        if self.transport is not None:
            if timeout is None:
                timeout = self.options["shutdown_timeout"]
            self.transport.flush(timeout=timeout, callback=callback)

    def __enter__(self):
        # type: () -> _Client
        return self

    def __exit__(self, exc_type, exc_value, tb):
        # type: (Any, Any, Any) -> None
        self.close()


MYPY = False
if MYPY:
    # Make mypy, PyCharm and other static analyzers think `get_options` is a
    # type to have nicer autocompletion for params.
    #
    # Use `ClientConstructor` to define the argument types of `init` and
    # `Dict[str, Any]` to tell static analyzers about the return type.

    class get_options(ClientConstructor, Dict[str, Any]):
        pass

    class Client(ClientConstructor, _Client):
        pass


else:
    # Alias `get_options` for actual usage. Go through the lambda indirection
    # to throw PyCharm off of the weakly typed signature (it would otherwise
    # discover both the weakly typed signature of `_init` and our faked `init`
    # type).

    get_options = (lambda: _get_options)()
    Client = (lambda: _Client)()
