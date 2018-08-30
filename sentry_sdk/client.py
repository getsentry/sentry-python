import os
import uuid
import random
import atexit
from datetime import datetime

from ._compat import string_types
from .utils import (
    strip_event,
    flatten_metadata,
    convert_types,
    handle_in_app,
    get_type_name,
    pop_hidden_keys,
    Dsn,
)
from .transport import Transport
from .consts import DEFAULT_OPTIONS, SDK_INFO


NO_DSN = object()


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
    def __init__(self, *args, **kwargs):
        options = get_options(*args, **kwargs)

        dsn = options["dsn"]
        if dsn is not None:
            dsn = Dsn(dsn)

        self.options = options
        self._transport = self.options.pop("transport")
        if self._transport is None and dsn is not None:
            self._transport = Transport(
                dsn=dsn,
                http_proxy=self.options.pop("http_proxy"),
                https_proxy=self.options.pop("https_proxy"),
            )
            self._transport.start()

        request_bodies = ("always", "never", "small", "medium")
        if options["request_bodies"] not in request_bodies:
            raise ValueError(
                "Invalid value for request_bodies. Must be one of {}".format(
                    request_bodies
                )
            )

        atexit.register(self.close)

    @property
    def dsn(self):
        """The DSN that created this event."""
        if self._transport is not None:
            return self._transport.dsn

    @classmethod
    def disabled(cls):
        """Creates a guarnateed to be disabled client."""
        return cls(NO_DSN)

    def _prepare_event(self, event, scope):
        if event.get("timestamp") is None:
            event["timestamp"] = datetime.utcnow()

        if scope is not None:
            event = scope.apply_to_event(event)
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
        event = strip_event(event)

        before_send = self.options["before_send"]
        if before_send is not None:
            event = before_send(event)

        if event is not None:
            pop_hidden_keys(event)
            event = flatten_metadata(event)
            event = convert_types(event)

        return event

    def _is_ignored_error(self, event):
        exc_info = event.get("__sentry_exc_info")

        if not exc_info or exc_info[0] is None:
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

    def _should_capture(self, event, scope=None):
        if (
            self.options["sample_rate"] < 1.0
            and random.random() >= self.options["sample_rate"]
        ):
            return False

        if self._is_ignored_error(event):
            return False

        return True

    def capture_event(self, event, scope=None):
        """Captures an event."""
        if self._transport is None:
            return
        rv = event.get("event_id")
        if rv is None:
            event["event_id"] = rv = uuid.uuid4().hex
        if self._should_capture(event, scope):
            event = self._prepare_event(event, scope)
            if event is not None:
                self._transport.capture_event(event)
        return True

    def drain_events(self, timeout=None):
        if timeout is None:
            timeout = self.options["shutdown_timeout"]
        if self._transport is not None:
            self._transport.drain_events(timeout)

    def close(self):
        self.drain_events()
        if self._transport is not None:
            self._transport.close()
