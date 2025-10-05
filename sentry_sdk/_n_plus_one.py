from contextvars import ContextVar
from contextlib import contextmanager
from typing import Iterator

# Context var that indicates whether the current execution context should
# ignore N+1 detection. When True, tracing code should mark spans accordingly.
_IGNORE_N_PLUS_ONE = ContextVar("sentry_ignore_n_plus_one", default=False)


@contextmanager
def ignore_n_plus_one_context() -> Iterator[None]:
    token = _IGNORE_N_PLUS_ONE.set(True)
    try:
        yield
    finally:
        _IGNORE_N_PLUS_ONE.reset(token)


def is_ignoring_n_plus_one() -> bool:
    return bool(_IGNORE_N_PLUS_ONE.get(False))


def set_ignore_n_plus_one(value: bool):
    # For testing purposes
    _IGNORE_N_PLUS_ONE.set(bool(value))
