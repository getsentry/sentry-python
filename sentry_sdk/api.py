from .hub import Hub
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
    install = setup_integrations(options)
    client = Client(options)
    hub.bind_client(client)
    install()
    return _InitGuard(client)


def init(*args, **kwargs):
    return _init_on_hub(Hub.main, args, kwargs)


def _init_on_current(*args, **kwargs):
    return _init_on_hub(Hub.current, args, kwargs)


from . import minimal as sentry_minimal

__all__ = ["Hub", "Scope", "Client", "init"] + sentry_minimal.__all__


for _key in sentry_minimal.__all__:
    globals()[_key] = getattr(sentry_minimal, _key)
    globals()[_key].__module__ = __name__
del _key
