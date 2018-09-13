import os
import uuid
import random
from datetime import datetime

from sentry_sdk._compat import string_types
from sentry_sdk.utils import (
    strip_event,
    flatten_metadata,
    convert_types,
    handle_in_app,
    get_type_name,
    capture_internal_exceptions,
    logger,
)
from sentry_sdk.transport import make_transport
from sentry_sdk.consts import DEFAULT_OPTIONS, SDK_INFO


def get_options(*args, **kwargs):
    if args and (isinstance(args[0], string_types) or args[0] is None):
        dsn = args[0]
        args = args[1:]
    else:
        dsn = None

    rv = dict(DEFAULT_OPTIONS)
    options = dict(*args, **kwargs)
    if dsn is not None and options.get("dsn") is None:
        options["dsn"] = dsn

    for key, value in options.items():
        if key not in rv:
            raise TypeError("Unknown option %r" % (key,))
        rv[key] = value

    if rv["dsn"] is None:
        rv["dsn"] = os.environ.get("SENTRY_DSN")

    return rv


class Client(object):
    """The client is internally responsible for capturing the events and
    forwarding them to sentry through the configured transport.  It takes
    the client options as keyword arguments and optionally the DSN as first
    argument.
    """

    def __init__(self, *args, **kwargs):
        self.options = options = get_options(*args, **kwargs)
        self.transport = make_transport(options)

        request_bodies = ("always", "never", "small", "medium")
        if options["request_bodies"] not in request_bodies:
            raise ValueError(
                "Invalid value for request_bodies. Must be one of {}".format(
                    request_bodies
                )
            )

    @property
    def dsn(self):
        """Returns the configured DSN as string."""
        return self.options["dsn"]

    def _prepare_event(self, event, hint, scope):
        if event.get("timestamp") is None:
            event["timestamp"] = datetime.utcnow()

        if scope is not None:
            event = scope.apply_to_event(event, hint)
            if event is None:
                return

        for key in "release", "environment", "server_name", "repos", "dist":
            if event.get(key) is None:
                event[key] = self.options[key]
        if event.get("sdk") is None:
            event["sdk"] = SDK_INFO

        if event.get("platform") is None:
            event["platform"] = "python"

        event = handle_in_app(
            event, self.options["in_app_exclude"], self.options["in_app_include"]
        )

        before_send = self.options["before_send"]
        if before_send is not None:
            with capture_internal_exceptions():
                new_event = before_send(event, hint)
            if new_event is None:
                logger.info("before send dropped event (%s)", event)
            event = new_event

        # Postprocess the event in the very end so that annotated types do
        # generally not surface in before_send
        if event is not None:
            event = strip_event(event)
            event = flatten_metadata(event)
            event = convert_types(event)

        return event

    def _is_ignored_error(self, event, hint=None):
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
                if issubclass(exc_info[0], errcls):
                    return True

        return False

    def _should_capture(self, event, hint, scope=None):
        if (
            self.options["sample_rate"] < 1.0
            and random.random() >= self.options["sample_rate"]
        ):
            return False

        if self._is_ignored_error(event, hint):
            return False

        return True

    def capture_event(self, event, hint=None, scope=None):
        """Captures an event.

        This takes the ready made event and an optoinal hint and scope.  The
        hint is internally used to further customize the representation of the
        error.  When provided it's a dictionary of optional information such
        as exception info.

        If the transport is not set nothing happens, otherwise the return
        value of this function will be the ID of the captured event.
        """
        if self.transport is None:
            return
        if hint is None:
            hint = {}
        rv = event.get("event_id")
        if rv is None:
            event["event_id"] = rv = uuid.uuid4().hex
        if self._should_capture(event, hint, scope):
            event = self._prepare_event(event, hint, scope)
            if event is not None:
                self.transport.capture_event(event)
        return rv

    def close(self, timeout=None, shutdown_callback=None):
        """Closes the client which shuts down the transport in an
        orderly manner.

        The `shutdown_callback` is invoked with two arguments: the number of
        pending events and the configured shutdown timeout.  For instance the
        default atexit integration will use this to render out a message on
        stderr.
        """
        if self.transport is not None:
            if timeout is None:
                timeout = self.options["shutdown_timeout"]
            self.transport.shutdown(timeout=timeout, callback=shutdown_callback)
            self.transport = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        self.close()
