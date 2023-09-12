from typing import ParamSpec, TypeVar, Callable

from sentry_sdk import Hub
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.hub import _should_send_default_pii
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.tracing import Span
from sentry_sdk.utils import capture_internal_exceptions


try:
    import clickhouse_driver  # type: ignore[import]

except ImportError:
    raise DidNotEnable("clickhouse-driver not installed.")

if clickhouse_driver.VERSION < (0, 2, 0):
    raise DidNotEnable("clickhouse-driver >= 0.2.0 required")


class ClickhouseDriverIntegration(Integration):
    identifier = "clickhouse_driver"

    @staticmethod
    def setup_once() -> None:
        # Every query is done using the Connection's `send_query` function
        clickhouse_driver.connection.Connection.send_query = _wrap_start(
            clickhouse_driver.connection.Connection.send_query
        )

        # If the query contains parameters then the send_data function is used to send those parameters to clickhouse
        clickhouse_driver.client.Client.send_data = _wrap_send_data(
            clickhouse_driver.client.Client.send_data
        )

        # Every query ends either with the Client's `receive_end_of_query` (no result expected)
        # or its `receive_result` (result expected)
        clickhouse_driver.client.Client.receive_end_of_query = _wrap_end(
            clickhouse_driver.client.Client.receive_end_of_query
        )
        clickhouse_driver.client.Client.receive_result = _wrap_end(
            clickhouse_driver.client.Client.receive_result
        )


P = ParamSpec("P")
T = TypeVar("T")


def _wrap_start(f: Callable[P, T]) -> Callable[P, T]:
    def _inner(*args: P.args, **kwargs: P.kwargs) -> T:
        hub = Hub.current
        if hub.get_integration(ClickhouseDriverIntegration) is None:
            return f(*args, **kwargs)
        connection = args[0]
        query = args[1]
        query_id = args[2] if len(args) > 2 else kwargs.get("query_id")
        params = args[3] if len(args) > 3 else kwargs.get("params")

        span = hub.start_span(op=OP.DB, description=query)

        connection._sentry_span = span  # type: ignore[attr-defined]

        _set_db_data(span, connection)

        span.set_data("query", query)

        if query_id:
            span.set_data("db.query_id", query_id)

        if params and _should_send_default_pii():
            span.set_data("db.params", params)

        # run the original code
        ret = f(*args, **kwargs)

        return ret

    return _inner


def _wrap_end(f: Callable[P, T]) -> Callable[P, T]:
    def _inner_end(*args: P.args, **kwargs: P.kwargs) -> T:
        res = f(*args, **kwargs)
        instance = args[0]
        span = instance.connection._sentry_span  # type: ignore[attr-defined]

        if span is not None:
            if res is not None and _should_send_default_pii():
                span.set_data("db.result", res)

            with capture_internal_exceptions():
                span.hub.add_breadcrumb(
                    message=span._data.pop("query"), category="query", data=span._data
                )

            span.finish()

        return res

    return _inner_end


def _wrap_send_data(f: Callable[P, T]) -> Callable[P, T]:
    def _inner_send_data(*args: P.args, **kwargs: P.kwargs) -> T:
        instance = args[0]
        data = args[2]
        span = instance.connection._sentry_span  # type: ignore[attr-defined]

        _set_db_data(span, instance.connection)

        if _should_send_default_pii():
            db_params = span._data.get("db.params", [])
            db_params.extend(data)
            span.set_data("db.params", db_params)

        return f(*args, **kwargs)

    return _inner_send_data


def _set_db_data(
    span: Span, connection: clickhouse_driver.connection.Connection
) -> None:
    span.set_data(SPANDATA.DB_SYSTEM, "clickhouse")
    span.set_data(SPANDATA.SERVER_ADDRESS, connection.host)
    span.set_data(SPANDATA.SERVER_PORT, connection.port)
    span.set_data(SPANDATA.DB_NAME, connection.database)
    span.set_data(SPANDATA.DB_USER, connection.user)
