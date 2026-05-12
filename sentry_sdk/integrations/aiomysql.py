# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, TypeVar, Callable, Awaitable

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import _check_minimum_version, Integration, DidNotEnable
from sentry_sdk.tracing_utils import (
    add_query_source,
    record_sql_queries_supporting_streaming,
)
from sentry_sdk.utils import (
    capture_internal_exceptions,
    parse_version,
)

try:
    import aiomysql  # type: ignore[import-untyped]
    from aiomysql.connection import Connection  # type: ignore[import-untyped]
    from aiomysql.cursors import Cursor  # type: ignore[import-untyped]
except ImportError:
    raise DidNotEnable("aiomysql not installed.")


class AioMySQLIntegration(Integration):
    identifier = "aiomysql"
    origin = f"auto.db.{identifier}"
    _record_params = False

    def __init__(self, *, record_params: bool = False):
        AioMySQLIntegration._record_params = record_params

    @staticmethod
    def setup_once() -> None:
        aiomysql_version = parse_version(aiomysql.__version__)
        _check_minimum_version(AioMySQLIntegration, aiomysql_version)

        Cursor.execute = _wrap_execute(Cursor.execute)
        Cursor.executemany = _wrap_executemany(Cursor.executemany)

        # Patch Connection._connect — this catches ALL connections:
        #   - aiomysql.connect()
        #   - aiomysql.create_pool() (pool.py does `from .connection import connect`
        #     which ultimately calls Connection._connect)
        #   - Reconnects
        Connection._connect = _wrap_connect(Connection._connect)


T = TypeVar("T")


def _normalize_query(query: str | bytes | bytearray) -> str:
    if isinstance(query, (bytes, bytearray)):
        query = query.decode("utf-8", errors="replace")
    return " ".join(query.split())


def _wrap_execute(f: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """Wrap Cursor.execute to capture SQL queries."""

    async def _inner(*args: Any, **kwargs: Any) -> T:
        if sentry_sdk.get_client().get_integration(AioMySQLIntegration) is None:
            return await f(*args, **kwargs)

        cursor = args[0]

        # Skip if flagged by executemany (avoids double-recording).
        # Do NOT reset the flag here — it must stay True for the entire
        # duration of executemany, which may call execute multiple times
        # in a loop (non-INSERT fallback). Only _wrap_executemany's
        # finally block should clear it.
        if getattr(cursor, "_sentry_skip_next_execute", False):
            return await f(*args, **kwargs)

        query = args[1] if len(args) > 1 else kwargs.get("query", "")
        query_str = _normalize_query(query)
        params = args[2] if len(args) > 2 else kwargs.get("args")

        conn = _get_connection(cursor)

        integration = sentry_sdk.get_client().get_integration(AioMySQLIntegration)
        params_list = params if integration and integration._record_params else None
        param_style = "pyformat" if params_list else None

        with record_sql_queries_supporting_streaming(
            cursor=None,
            query=query_str,
            params_list=params_list,
            paramstyle=param_style,
            executemany=False,
            span_origin=AioMySQLIntegration.origin,
        ) as span:
            if conn:
                _set_db_data(span, conn)
            res = await f(*args, **kwargs)

        with capture_internal_exceptions():
            add_query_source(span)

        return res

    return _inner


def _wrap_executemany(f: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """Wrap Cursor.executemany to capture SQL queries."""

    async def _inner(*args: Any, **kwargs: Any) -> T:
        if sentry_sdk.get_client().get_integration(AioMySQLIntegration) is None:
            return await f(*args, **kwargs)

        cursor = args[0]
        query = args[1] if len(args) > 1 else kwargs.get("query", "")
        query_str = _normalize_query(query)
        seq_of_params = args[2] if len(args) > 2 else kwargs.get("args")

        conn = _get_connection(cursor)

        integration = sentry_sdk.get_client().get_integration(AioMySQLIntegration)
        params_list = (
            seq_of_params if integration and integration._record_params else None
        )
        param_style = "pyformat" if params_list else None

        # Prevent double-recording: _do_execute_many calls self.execute internally
        cursor._sentry_skip_next_execute = True
        try:
            with record_sql_queries_supporting_streaming(
                cursor=None,
                query=query_str,
                params_list=params_list,
                paramstyle=param_style,
                executemany=True,
                span_origin=AioMySQLIntegration.origin,
            ) as span:
                if conn:
                    _set_db_data(span, conn)
                res = await f(*args, **kwargs)

            with capture_internal_exceptions():
                add_query_source(span)

            return res
        finally:
            cursor._sentry_skip_next_execute = False

    return _inner


def _get_connection(cursor: Any) -> Any:
    """Get the underlying connection from a cursor."""
    return getattr(cursor, "connection", None)


def _wrap_connect(f: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """Wrap Connection._connect to capture connection spans."""

    async def _inner(self: "Connection") -> T:
        if sentry_sdk.get_client().get_integration(AioMySQLIntegration) is None:
            return await f(self)

        with sentry_sdk.start_span(
            op=OP.DB,
            name="connect",
            origin=AioMySQLIntegration.origin,
        ) as span:
            _set_db_data(span, self)

            with capture_internal_exceptions():
                sentry_sdk.add_breadcrumb(
                    message="connect",
                    category="query",
                    data=span._data,
                )
            res = await f(self)

        return res

    return _inner


def _set_db_data(span: Any, conn: Any) -> None:
    """Set database-related span data from connection object."""
    span.set_data(SPANDATA.DB_SYSTEM, "mysql")

    host = getattr(conn, "host", None)
    if host is not None:
        span.set_data(SPANDATA.SERVER_ADDRESS, host)

    port = getattr(conn, "port", None)
    if port is not None:
        span.set_data(SPANDATA.SERVER_PORT, port)

    database = getattr(conn, "db", None)
    if database is not None:
        span.set_data(SPANDATA.DB_NAME, database)

    user = getattr(conn, "user", None)
    if user is not None:
        span.set_data(SPANDATA.DB_USER, user)
