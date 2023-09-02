from typing import ParamSpec, TypeVar, Callable

from asyncpg.cursor import BaseCursor, CursorIterator

from sentry_sdk import Hub
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.tracing_utils import record_sql_queries
from sentry_sdk.utils import parse_version

try:
    import asyncpg  # type: ignore[import]

except ImportError:
    raise DidNotEnable("asyncpg not installed.")

# asyncpg.__version__ is a string containing the semantic version in the form of "<major>.<minor>.<patch>"
asyncpg_version = parse_version(asyncpg.__version__)

if asyncpg_version < (0, 23, 0):
    raise DidNotEnable("asyncpg >= 0.23.0 required")


class AsyncPGIntegration(Integration):
    identifier = "asyncpg"

    def __init__(self, *, record_params=False):
        AsyncPGIntegration._record_params = record_params

    @staticmethod
    def setup_once() -> None:
        asyncpg.Connection._execute = _wrap_connection_method(
            asyncpg.Connection._execute
        )
        asyncpg.Connection._executemany = _wrap_connection_method(
            asyncpg.Connection._executemany, executemany=True
        )
        asyncpg.connection.cursor.BaseCursor._exec = _wrap_basecursor_exec(
            asyncpg.connection.cursor.BaseCursor._exec
        )
        asyncpg.connection.cursor.CursorIterator.__anext__ = _wrap_cursoriterator_anext(
            asyncpg.connection.cursor.CursorIterator.__anext__
        )
        asyncpg.Connection.prepare = _wrap_connection_method(asyncpg.Connection.prepare)


P = ParamSpec("P")
T = TypeVar("T")


def _wrap_connection_method(f: Callable[P, T], *, executemany=False) -> Callable[P, T]:
    async def _inner(*args: P.args, **kwargs: P.kwargs) -> T:
        hub = Hub.current
        integration = hub.get_integration(AsyncPGIntegration)

        if integration is None:
            return await f(*args, **kwargs)

        record_params = integration._record_params

        query = args[1]
        params_list = args[2] if record_params else None
        param_style = "pyformat" if params_list else None
        with record_sql_queries(
            hub, None, query, params_list, param_style, executemany=executemany
        ):
            res = await f(*args, **kwargs)
        return res

    return _inner


def _wrap_basecursor_exec(f: Callable[P, T]) -> Callable[P, T]:
    async def _exec(self: BaseCursor, n, timeout):
        hub = Hub.current
        integration = hub.get_integration(AsyncPGIntegration)

        if integration is None:
            return await f(self, n, timeout)

        record_params = integration._record_params
        params_list = self._args[1] if record_params else None
        param_style = "pyformat" if params_list else None

        executemany = n > 1

        with record_sql_queries(
            hub,
            self,
            self._query,
            params_list,
            param_style,
            executemany=executemany,
            record_cursor_repr=True,
        ):
            res = await f(self, n, timeout)
        return res

    return _exec


def _wrap_cursoriterator_anext(f: Callable[P, T]) -> Callable[P, T]:
    async def __await__(self: CursorIterator):
        hub = Hub.current
        integration = hub.get_integration(AsyncPGIntegration)

        if integration is None:
            return await f(self)

        record_params = integration._record_params
        params_list = self._args[1] if record_params else None
        param_style = "pyformat" if params_list else None

        with record_sql_queries(
            hub,
            self,
            self._query,
            params_list,
            param_style,
            executemany=False,
            record_cursor_repr=True,
        ) as span:
            try:
                res = await f(self)
            except StopAsyncIteration:
                span.set_data("db.cursor.exhausted", True)
                raise StopAsyncIteration

        return res

    return __await__
