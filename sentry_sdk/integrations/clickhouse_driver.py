import functools
from typing import TYPE_CHECKING, TypeVar

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.utils import has_data_collection_enabled

# Hack to get new Python features working in older versions
# without introducing a hard dependency on `typing_extensions`
# from: https://stackoverflow.com/a/71944042/300572
if TYPE_CHECKING:
    from typing import Any, Callable, Optional, ParamSpec
else:
    # Fake ParamSpec
    class ParamSpec:
        def __init__(self, _):
            self.args = None
            self.kwargs = None

    # Callable[anything] will return None
    class _Callable:
        def __getitem__(self, _):
            return None

    # Make instances
    Callable = _Callable()


try:
    from clickhouse_driver import VERSION  # type: ignore[import-not-found]
    from clickhouse_driver.client import Client  # type: ignore[import-not-found]
    from clickhouse_driver.connection import (  # type: ignore[import-not-found]
        Connection,
    )

except ImportError:
    raise DidNotEnable("clickhouse-driver not installed or incompatible")


class ClickhouseDriverIntegration(Integration):
    identifier = "clickhouse_driver"
    origin = f"auto.db.{identifier}"

    @staticmethod
    def setup_once() -> None:
        _check_minimum_version(ClickhouseDriverIntegration, VERSION)

        # Every query is done using the Connection's `send_query` function
        Connection.send_query = _wrap_start(Connection.send_query)

        # If the query contains parameters then the send_data function is used to send those parameters to clickhouse
        _wrap_send_data()

        # Every query ends either with the Client's `receive_end_of_query` (no result expected)
        # or its `receive_result` (result expected)
        Client.receive_end_of_query = _wrap_end(Client.receive_end_of_query)
        if hasattr(Client, "receive_end_of_insert_query"):
            # In 0.2.7, insert queries are handled separately via `receive_end_of_insert_query`
            Client.receive_end_of_insert_query = _wrap_end(
                Client.receive_end_of_insert_query
            )
        Client.receive_result = _wrap_end(Client.receive_result)


P = ParamSpec("P")
T = TypeVar("T")


def _wrap_start(f: "Callable[P, T]") -> "Callable[P, T]":
    @functools.wraps(f)
    def _inner(*args: "P.args", **kwargs: "P.kwargs") -> "T":
        client = sentry_sdk.get_client()
        if client.get_integration(ClickhouseDriverIntegration) is None:
            return f(*args, **kwargs)

        connection: "Connection" = args[0]
        query = args[1]

        span = None
        if sentry_sdk.traces.get_current_span() is not None:
            span = sentry_sdk.traces.start_span(
                name=query,  # type: ignore
                attributes={
                    "sentry.op": OP.DB,
                    "sentry.origin": ClickhouseDriverIntegration.origin,
                    SPANDATA.DB_QUERY_TEXT: str(query),
                },
            )

        connection._query = query
        connection._breadcrumb_data = {
            SPANDATA.DB_SYSTEM: "clickhouse",
            SPANDATA.DB_NAME: connection.database,
            SPANDATA.DB_DRIVER_NAME: "clickhouse-driver",
            SPANDATA.SERVER_ADDRESS: connection.host,
            SPANDATA.SERVER_PORT: connection.port,
            SPANDATA.DB_USER: connection.user,
        }

        connection._sentry_span = span

        if span is not None:
            _set_db_data(span, connection)

        # run the original code
        ret = f(*args, **kwargs)

        return ret

    return _inner


def _wrap_end(f: "Callable[P, T]") -> "Callable[P, T]":
    def _inner_end(*args: "P.args", **kwargs: "P.kwargs") -> "T":
        res = f(*args, **kwargs)
        instance: "Client" = args[0]

        query = getattr(instance.connection, "_query", None)
        breadcrumb_data: "Optional[dict[str, Any]]" = getattr(
            instance.connection, "_breadcrumb_data", None
        )

        if query is not None and breadcrumb_data is not None:
            client_options = sentry_sdk.get_client().options
            if (
                has_data_collection_enabled(client_options)
                and client_options["data_collection"]["database_query_data"]
            ) or (
                not has_data_collection_enabled(client_options)
                and should_send_default_pii()
            ):
                breadcrumb_data = {"db.result": res, **breadcrumb_data}

            sentry_sdk.get_isolation_scope().add_breadcrumb(
                message=query,
                category="query",
                data=breadcrumb_data,
            )

        span = getattr(instance.connection, "_sentry_span", None)

        if span is None:
            return res

        span.end()

        return res

    return _inner_end


def _wrap_send_data() -> None:
    original_send_data = Client.send_data

    def _inner_send_data(  # type: ignore[no-untyped-def] # clickhouse-driver does not type send_data
        self, sample_block, data, types_check=False, columnar=False, *args, **kwargs
    ):
        span: "Optional[StreamedSpan]" = getattr(self.connection, "_sentry_span", None)

        _set_db_data(span, self.connection)
        return original_send_data(
            self, sample_block, data, types_check, columnar, *args, **kwargs
        )

    Client.send_data = _inner_send_data


def _set_db_data(span: "Optional[StreamedSpan]", connection: "Connection") -> None:
    if span is None:
        return

    span.set_attribute(SPANDATA.DB_DRIVER_NAME, "clickhouse-driver")
    span.set_attribute(SPANDATA.SERVER_ADDRESS, connection.host)
    span.set_attribute(SPANDATA.SERVER_PORT, connection.port)
    span.set_attribute(SPANDATA.DB_USER, connection.user)
