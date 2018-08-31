from .hub import Hub
from .utils import EventHint
from .client import Client, get_options
from .integrations import setup_integrations


class _InitGuard(object):
    def __init__(self, client):
        self._client = client

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        c = self._client
        if c is not None:
            c.close()


def _init_on_hub(hub, args, kwargs):
    options = get_options(*args, **kwargs)
    client = Client(options)
    hub.bind_client(client)
    setup_integrations(options)
    return _InitGuard(client)


def init(*args, **kwargs):
    """Initializes the SDK and optionally integrations."""
    return _init_on_hub(Hub.main, args, kwargs)


def _init_on_current(*args, **kwargs):
    # This function only exists to support unittests.  Do not call it as
    # initializing integrations on anything but the main hub is not going
    # to yield the results you expect.
    return _init_on_hub(Hub.current, args, kwargs)


from . import minimal as sentry_minimal
from .minimal import *  # noqa

__all__ = ["Hub", "Scope", "Client", "EventHint", "init"]
__all__ += sentry_minimal.__all__
