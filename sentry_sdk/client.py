import os
import uuid

from .utils import Dsn, SkipEvent
from .transport import Transport
from .consts import DEFAULT_OPTIONS, SDK_INFO
from .stripping import strip_event, flatten_metadata
from .integrations import get_integration


NO_DSN = object()


class Client(object):
    def __init__(self, dsn=None, *args, **kwargs):
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
        if dsn is None:
            self._transport = None
        else:
            self._transport = Transport(dsn)
            self._transport.start()

        integrations = options.get("integrations", ["logging"]) or ()
        if not isinstance(integrations, dict):
            integrations = {name: {} for name in integrations}

        for name, options in integrations.items():
            install_fn = get_integration(name)
            if options is None:
                options = {}
            install_fn(self, **options)

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

        for key in "release", "environment", "server_name":
            if event.get(key) is None:
                event[key] = self.options[key]
        if event.get("sdk") is None:
            event["sdk"] = SDK_INFO

        if event.get("platform") is None:
            event["platform"] = "python"

        event = strip_event(event)
        event = flatten_metadata(event)
        return event

    def capture_event(self, event, scope=None):
        """Captures an event."""
        if self._transport is None:
            return
        try:
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
