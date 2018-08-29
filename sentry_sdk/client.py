import os
import uuid
import random
import atexit
from datetime import datetime

from .utils import (
    strip_event,
    flatten_metadata,
    convert_types,
    handle_in_app,
    Dsn,
    ContextVar,
)
from .transport import Transport
from .consts import DEFAULT_OPTIONS, SDK_INFO


NO_DSN = object()


def _get_default_integrations():
    from .integrations.logging import LoggingIntegration
    from .integrations.excepthook import ExcepthookIntegration

    yield LoggingIntegration
    yield ExcepthookIntegration


class Client(object):
    def __init__(self, dsn=None, *args, **kwargs):
        passed_dsn = dsn
        if dsn is NO_DSN:
            dsn = None
        else:
            if dsn is None:
                dsn = os.environ.get("SENTRY_DSN")
            if not dsn:
                dsn = None
            else:
                dsn = Dsn(dsn)
        options = dict(DEFAULT_OPTIONS)
        options.update(*args, **kwargs)
        self.options = options
        self._transport = self.options.pop("transport")
        if self._transport is None and dsn is not None:
            self._transport = Transport(
                dsn=dsn,
                http_proxy=self.options.pop("http_proxy"),
                https_proxy=self.options.pop("https_proxy"),
            )
            self._transport.start()
        elif passed_dsn is not None and self._transport is not None:
            raise ValueError("Cannot pass DSN and a custom transport.")

        integrations = list(options.pop("integrations") or ())

        if options["default_integrations"]:
            for cls in _get_default_integrations():
                if not any(isinstance(x, cls) for x in integrations):
                    integrations.append(cls())

        for integration in integrations:
            integration(self)

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
            scope.apply_to_event(event)

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
            event = flatten_metadata(event)
            event = convert_types(event)

        return event

    def _should_capture(self, event):
        return not (
            self.options["sample_rate"] < 1.0
            and random.random() >= self.options["sample_rate"]
        )

    def capture_event(self, event, scope=None):
        """Captures an event."""
        if self._transport is None:
            return
        rv = event.get("event_id")
        if rv is None:
            event["event_id"] = rv = uuid.uuid4().hex
        if self._should_capture(event):
            event = self._prepare_event(event, scope)
            if event is not None:
                self._transport.capture_event(event)
        return rv

    def drain_events(self, timeout=None):
        if timeout is None:
            timeout = self.options["shutdown_timeout"]
        if self._transport is not None:
            self._transport.drain_events(timeout)

    def close(self):
        self.drain_events()
        if self._transport is not None:
            self._transport.close()
