from .hub import Hub
from .client import Client


class _InitGuard(object):
    def __init__(self, client):
        self._client = client

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        c = self._client
        if c is not None:
            c.close()


def init(*args, **kwargs):
    client = Client(*args, **kwargs)
    Hub.main.bind_client(client)
    return _InitGuard(client)


from . import minimal as sentry_minimal

__all__ = ["Hub", "Scope", "Client", "init"] + sentry_minimal.__all__


for _key in sentry_minimal.__all__:
    globals()[_key] = getattr(sentry_minimal, _key)
    globals()[_key].__module__ = __name__
del _key
