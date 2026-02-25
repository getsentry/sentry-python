# -*- coding: utf-8 -*-
"""
Adapted from module sentry_sdk.integrations.asyncpg
"""
from __future__ import annotations
import contextlib
from typing import Any, TypeVar, Callable, Awaitable, Iterator

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import _check_minimum_version, Integration, DidNotEnable
from sentry_sdk.tracing import Span
from sentry_sdk.tracing_utils import add_query_source, record_sql_queries
from sentry_sdk.utils import (
  ensure_integration_enabled,
  parse_version,
  capture_internal_exceptions,
)

try:
  import aiomysql  # type: ignore[import-not-found]
  from aiomysql.connection import Connection, Cursor  # type: ignore
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

    aiomysql.Connection.query = _wrap_execute(
      aiomysql.Connection.query,
    )

    aiomysql.connect = _wrap_connect(aiomysql.connect)


T = TypeVar("T")


def _wrap_execute(f: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
  async def _inner(*args: Any, **kwargs: Any) -> T:
    if sentry_sdk.get_client().get_integration(AioMySQLIntegration) is None:
      return await f(*args, **kwargs)

    conn = args[0]
    query = args[1]  # В aiomysql запрос передается первым аргументом
    with record_sql_queries(
      cursor=None,
      query=query,
      params_list=None,
      paramstyle=None,
      executemany=False,
      span_origin=AioMySQLIntegration.origin,
    ) as span:
      res = await f(*args, **kwargs)
      span.set_data("db.affected_rows", res)

    with capture_internal_exceptions():
      add_query_source(span)

    return res

  return _inner


SubCursor = TypeVar("SubCursor", bound=Cursor)


@contextlib.contextmanager
def _record(
  cursor: SubCursor | None,
  query: str,
  params_list: tuple[Any, ...] | None,
  *,
  executemany: bool = False,
) -> Iterator[Span]:
  integration = sentry_sdk.get_client().get_integration(AioMySQLIntegration)
  if integration is not None and not integration._record_params:
    params_list = None

  param_style = "pyformat" if params_list else None

  with record_sql_queries(
    cursor=cursor,
    query=query,
    params_list=params_list,
    paramstyle=param_style,
    executemany=executemany,
    record_cursor_repr=cursor is not None,
    span_origin=AioMySQLIntegration.origin,
  ) as span:
    yield span


def _wrap_connect(f: Callable[..., T]) -> Callable[..., T]:
  def _inner(*args: Any, **kwargs: Any) -> T:
    if sentry_sdk.get_client().get_integration(AioMySQLIntegration) is None:
      return f(*args, **kwargs)

    host = kwargs.get("host", "localhost")
    port = kwargs.get("port") or 3306
    user = kwargs.get("user")
    db = kwargs.get("db")

    with sentry_sdk.start_span(
      op=OP.DB,
      name="connect",
      origin=AioMySQLIntegration.origin,
    ) as span:
      span.set_data(SPANDATA.DB_SYSTEM, "mysql")
      span.set_data(SPANDATA.SERVER_ADDRESS, host)
      span.set_data(SPANDATA.SERVER_PORT, port)
      span.set_data(SPANDATA.DB_NAME, db)
      span.set_data(SPANDATA.DB_USER, user)

      with capture_internal_exceptions():
        sentry_sdk.add_breadcrumb(
          message="connect", category="query", data=span._data
        )
      res = f(*args, **kwargs)

    return res

  return _inner


def _set_db_data(span: Span, conn: Any) -> None:
  span.set_data(SPANDATA.DB_SYSTEM, "mysql")

  host = conn.host
  if host:
    span.set_data(SPANDATA.SERVER_ADDRESS, host)

  port = conn.port
  if port:
    span.set_data(SPANDATA.SERVER_PORT, port)

  database = conn.db
  if database:
    span.set_data(SPANDATA.DB_NAME, database)

  user = conn.user
  if user:
    span.set_data(SPANDATA.DB_USER, user)
