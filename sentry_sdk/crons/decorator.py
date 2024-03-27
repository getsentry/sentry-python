from sentry_sdk._compat import PY2

if PY2:
    from sentry_sdk.crons._decorator_py2 import monitor
else:
    from sentry_sdk.crons._decorator import monitor


__all__ = [
    monitor,
]
