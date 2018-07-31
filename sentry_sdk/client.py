import os
import uuid
import random
import atexit

from .utils import Dsn, SkipEvent, ContextVar
from .transport import Transport
from .consts import DEFAULT_OPTIONS, SDK_INFO
from .stripping import strip_event, flatten_metadata


NO_DSN = object()

_most_recent_exception = ContextVar("sentry_most_recent_exception")


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

        from .integrations import logging as logging_integration

        integrations = list(options.pop("integrations") or ())

        logging_configured = any(
            isinstance(x, logging_integration.LoggingIntegration) for x in integrations
        )
        if not logging_configured and options["default_integrations"]:
            integrations.append(logging_integration.LoggingIntegration())

        for integration in integrations:
            integration(self)

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
        if event.get("event_id") is None:
            event["event_id"] = uuid.uuid4().hex

        if scope is not None:
            scope.apply_to_event(event)

        for key in "release", "environment", "server_name", "repos", "dist":
            if event.get(key) is None:
                event[key] = self.options[key]
        if event.get("sdk") is None:
            event["sdk"] = SDK_INFO

        if event.get("platform") is None:
            event["platform"] = "python"

        event = strip_event(event)
        event = flatten_metadata(event)
        return event

    def _check_should_capture(self, event):
        if (
            self.options["sample_rate"] < 1.0
            and random.random() >= self.options["sample_rate"]
        ):
            raise SkipEvent()

        if event._exc_value is not None:
            exclusions = self.options["ignore_errors"]
            exc_type = type(event._exc_value)

            if any(issubclass(exc_type, e) for e in exclusions):
                raise SkipEvent()

            if _most_recent_exception.get(None) is event._exc_value:
                raise SkipEvent()
            _most_recent_exception.set(event._exc_value)

    def capture_event(self, event, scope=None):
        """Captures an event."""
        if self._transport is None:
            return
        try:
            self._check_should_capture(event)
            event = self._prepare_event(event, scope)
        except SkipEvent:
            return
        self._transport.capture_event(event)

    def drain_events(self, timeout=None):
        if timeout is None:
            timeout = self.options["drain_timeout"]
        if self._transport is not None:
            self._transport.drain_events(timeout)

    def close(self):
        self.drain_events()
        if self._transport is not None:
            self._transport.close()
