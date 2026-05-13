from __future__ import annotations

import contextlib
import re
from typing import Any, Awaitable, Callable, Iterator, TypeVar, Union

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing import Span
from sentry_sdk.tracing_utils import (
    add_query_source,
    has_span_streaming_enabled,
    record_sql_queries_supporting_streaming,
)
from sentry_sdk.utils import (
    capture_internal_exceptions,
    parse_version,
)

try:
    import asyncpg  # type: ignore[import-not-found]
    from asyncpg.cursor import BaseCursor  # type: ignore

except ImportError:
    raise DidNotEnable("asyncpg not installed.")


class AsyncPGIntegration(Integration):
    identifier = "asyncpg"
    origin = f"auto.db.{identifier}"
    _record_params = False

    def __init__(self, *, record_params: bool = False):
        AsyncPGIntegration._record_params = record_params

    @staticmethod
    def setup_once() -> None:
        # asyncpg.__version__ is a string containing the semantic version in the form of "<major>.<minor>.<patch>"
        asyncpg_version = parse_version(asyncpg.__version__)
        _check_minimum_version(AsyncPGIntegration, asyncpg_version)

        asyncpg.Connection.execute = _wrap_execute(
            asyncpg.Connection.execute,
        )

        asyncpg.Connection._execute = _wrap_connection_method(
            asyncpg.Connection._execute
        )
        asyncpg.Connection._executemany = _wrap_connection_method(
            asyncpg.Connection._executemany, executemany=True
        )
        asyncpg.Connection.prepare = _wrap_connection_method(asyncpg.Connection.prepare)

        BaseCursor._bind_exec = _wrap_cursor_method(BaseCursor._bind_exec)
        BaseCursor._exec = _wrap_cursor_method(BaseCursor._exec)

        asyncpg.connect_utils._connect_addr = _wrap_connect_addr(
            asyncpg.connect_utils._connect_addr
        )


T = TypeVar("T")


def _normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", query).strip()


def _wrap_execute(f: "Callable[..., Awaitable[T]]") -> "Callable[..., Awaitable[T]]":
    async def _inner(*args: "Any", **kwargs: "Any") -> "T":
        client = sentry_sdk.get_client()
        if client.get_integration(AsyncPGIntegration) is None:
            return await f(*args, **kwargs)

        # Avoid recording calls to _execute twice.
        # Calls to Connection.execute with args also call
        # Connection._execute, which is recorded separately
        # args[0] = the connection object, args[1] is the query
        if len(args) > 2:
            return await f(*args, **kwargs)

        query = _normalize_query(args[1])
        with record_sql_queries_supporting_streaming(
            cursor=None,
            query=query,
            params_list=None,
            paramstyle=None,
            executemany=False,
            span_origin=AsyncPGIntegration.origin,
        ) as span:
            res = await f(*args, **kwargs)
            if isinstance(span, StreamedSpan):
                with capture_internal_exceptions():
                    add_query_source(span)

        if not isinstance(span, StreamedSpan):
            with capture_internal_exceptions():
                add_query_source(span)

        return res

    return _inner


SubCursor = TypeVar("SubCursor", bound=BaseCursor)


@contextlib.contextmanager
def _record(
    cursor: "SubCursor | None",
    query: str,
    params_list: "tuple[Any, ...] | None",
    *,
    executemany: bool = False,
) -> "Iterator[Union[Span, StreamedSpan]]":
    client = sentry_sdk.get_client()
    integration = client.get_integration(AsyncPGIntegration)
    if integration is not None and not integration._record_params:
        params_list = None

    param_style = "pyformat" if params_list else None

    query = _normalize_query(query)
    with record_sql_queries_supporting_streaming(
        cursor=cursor,
        query=query,
        params_list=params_list,
        paramstyle=param_style,
        executemany=executemany,
        record_cursor_repr=cursor is not None,
        span_origin=AsyncPGIntegration.origin,
    ) as span:
        yield span


def _wrap_connection_method(
    f: "Callable[..., Awaitable[T]]", *, executemany: bool = False
) -> "Callable[..., Awaitable[T]]":
    async def _inner(*args: "Any", **kwargs: "Any") -> "T":
        if sentry_sdk.get_client().get_integration(AsyncPGIntegration) is None:
            return await f(*args, **kwargs)
        query = args[1]
        params_list = args[2] if len(args) > 2 else None
        with _record(None, query, params_list, executemany=executemany) as span:
            _set_db_data(span, args[0])

            res = await f(*args, **kwargs)

            if isinstance(span, StreamedSpan):
                with capture_internal_exceptions():
                    add_query_source(span)

        if not isinstance(span, StreamedSpan):
            with capture_internal_exceptions():
                add_query_source(span)

        return res

    return _inner


def _wrap_cursor_method(
    f: "Callable[..., Awaitable[T]]",
) -> "Callable[..., Awaitable[T]]":
    async def _inner(*args: "Any", **kwargs: "Any") -> "T":
        if sentry_sdk.get_client().get_integration(AsyncPGIntegration) is None:
            return await f(*args, **kwargs)

        cursor = args[0]
        query = _normalize_query(cursor._query)
        with record_sql_queries_supporting_streaming(
            cursor=cursor,
            query=query,
            params_list=None,
            paramstyle=None,
            executemany=False,
            record_cursor_repr=True,
            span_origin=AsyncPGIntegration.origin,
        ) as span:
            _set_db_data(span, cursor._connection)
            res = await f(*args, **kwargs)

            if isinstance(span, StreamedSpan):
                with capture_internal_exceptions():
                    add_query_source(span)

        if not isinstance(span, StreamedSpan):
            with capture_internal_exceptions():
                add_query_source(span)

        return res

    return _inner


def _wrap_connect_addr(
    f: "Callable[..., Awaitable[T]]",
) -> "Callable[..., Awaitable[T]]":
    async def _inner(*args: "Any", **kwargs: "Any") -> "T":
        client = sentry_sdk.get_client()
        if client.get_integration(AsyncPGIntegration) is None:
            return await f(*args, **kwargs)

        user = kwargs["params"].user
        database = kwargs["params"].database
        addr = kwargs.get("addr")

        if has_span_streaming_enabled(client.options):
            span_attributes = {
                "sentry.op": OP.DB,
                "sentry.origin": AsyncPGIntegration.origin,
                SPANDATA.DB_SYSTEM: "postgresql",
                SPANDATA.DB_USER: user,
                SPANDATA.DB_NAME: database,
                SPANDATA.DB_DRIVER_NAME: "asyncpg",
            }
            if addr:
                try:
                    span_attributes[SPANDATA.SERVER_ADDRESS] = addr[0]
                    span_attributes[SPANDATA.SERVER_PORT] = addr[1]
                except IndexError:
                    pass

            with sentry_sdk.traces.start_span(
                name="connect", attributes=span_attributes
            ) as span:
                with capture_internal_exceptions():
                    sentry_sdk.add_breadcrumb(
                        message="connect", category="query", data=span_attributes
                    )
                res = await f(*args, **kwargs)

        else:
            with sentry_sdk.start_span(
                op=OP.DB,
                name="connect",
                origin=AsyncPGIntegration.origin,
            ) as span:
                span.set_data(SPANDATA.DB_SYSTEM, "postgresql")
                if addr:
                    try:
                        span.set_data(SPANDATA.SERVER_ADDRESS, addr[0])
                        span.set_data(SPANDATA.SERVER_PORT, addr[1])
                    except IndexError:
                        pass
                span.set_data(SPANDATA.DB_NAME, database)
                span.set_data(SPANDATA.DB_USER, user)
                span.set_data(SPANDATA.DB_DRIVER_NAME, "asyncpg")

                with capture_internal_exceptions():
                    sentry_sdk.add_breadcrumb(
                        message="connect", category="query", data=span._data
                    )
                res = await f(*args, **kwargs)

        return res

    return _inner


def _set_db_data(span: "Union[Span, StreamedSpan]", conn: "Any") -> None:
    set_value = span.set_attribute if isinstance(span, StreamedSpan) else span.set_data

    set_value(SPANDATA.DB_SYSTEM, "postgresql")
    set_value(SPANDATA.DB_DRIVER_NAME, "asyncpg")

    addr = conn._addr
    if addr:
        try:
            set_value(SPANDATA.SERVER_ADDRESS, addr[0])
            set_value(SPANDATA.SERVER_PORT, addr[1])
        except IndexError:
            pass

    database = conn._params.database
    if database:
        set_value(SPANDATA.DB_NAME, database)

    user = conn._params.user
    if user:
        set_value(SPANDATA.DB_USER, user)
