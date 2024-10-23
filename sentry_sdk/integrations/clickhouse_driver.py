import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.tracing import Span
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import (
    _serialize_span_attribute,
    capture_internal_exceptions,
    ensure_integration_enabled,
)

from typing import TYPE_CHECKING, Any, TypeVar

# Hack to get new Python features working in older versions
# without introducing a hard dependency on `typing_extensions`
# from: https://stackoverflow.com/a/71944042/300572
if TYPE_CHECKING:
    from typing import ParamSpec, Callable
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
    import clickhouse_driver  # type: ignore[import-not-found]

except ImportError:
    raise DidNotEnable("clickhouse-driver not installed.")

if clickhouse_driver.VERSION < (0, 2, 0):
    raise DidNotEnable("clickhouse-driver >= 0.2.0 required")


class ClickhouseDriverIntegration(Integration):
    identifier = "clickhouse_driver"
    origin = f"auto.db.{identifier}"

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
        if hasattr(clickhouse_driver.client.Client, "receive_end_of_insert_query"):
            # In 0.2.7, insert queries are handled separately via `receive_end_of_insert_query`
            clickhouse_driver.client.Client.receive_end_of_insert_query = _wrap_end(
                clickhouse_driver.client.Client.receive_end_of_insert_query
            )
        clickhouse_driver.client.Client.receive_result = _wrap_end(
            clickhouse_driver.client.Client.receive_result
        )


P = ParamSpec("P")
T = TypeVar("T")


def _wrap_start(f: Callable[P, T]) -> Callable[P, T]:
    @ensure_integration_enabled(ClickhouseDriverIntegration, f)
    def _inner(*args: P.args, **kwargs: P.kwargs) -> T:
        connection = args[0]
        query = args[1]
        query_id = args[2] if len(args) > 2 else kwargs.get("query_id")
        params = args[3] if len(args) > 3 else kwargs.get("params")

        span = sentry_sdk.start_span(
            op=OP.DB,
            name=query,
            origin=ClickhouseDriverIntegration.origin,
        )

        connection._sentry_span = span  # type: ignore[attr-defined]

        data = _get_db_data(connection)
        data["db.query.text"] = query

        if query_id:
            data["db.query_id"] = query_id

        if params and should_send_default_pii():
            data["db.params"] = params

        connection._sentry_db_data = data  # type: ignore[attr-defined]
        _set_on_span(span, data)

        # run the original code
        ret = f(*args, **kwargs)

        return ret

    return _inner


def _wrap_end(f: Callable[P, T]) -> Callable[P, T]:
    def _inner_end(*args: P.args, **kwargs: P.kwargs) -> T:
        res = f(*args, **kwargs)
        connection = args[0].connection

        span = getattr(connection, "_sentry_span", None)  # type: ignore[attr-defined]
        if span is not None:
            data = getattr(connection, "_sentry_db_data", {})

            if res is not None and should_send_default_pii():
                serialized_result = _serialize_span_attribute(res)
                data["db.result"] = serialized_result
                span.set_attribute("db.result", serialized_result)

            with capture_internal_exceptions():
                query = data.pop("db.query.text", None)
                if query:
                    sentry_sdk.add_breadcrumb(
                        message=query, category="query", data=data
                    )

            span.finish()

        return res

    return _inner_end


def _wrap_send_data(f: Callable[P, T]) -> Callable[P, T]:
    def _inner_send_data(*args: P.args, **kwargs: P.kwargs) -> T:
        instance = args[0]  # type: clickhouse_driver.client.Client
        db_params_data = args[2]
        span = getattr(instance.connection, "_sentry_span", None)

        if span is not None:
            data = _get_db_data(instance.connection)
            _set_on_span(span, data)

            if should_send_default_pii():
                db_params = (
                    getattr(instance.connection, "_sentry_db_data", {}).get("db.params")
                    or []
                )
                db_params.extend(db_params_data)
                span.set_attribute("db.params", _serialize_span_attribute(db_params))
                try:
                    del instance.connection._sentry_db_data
                except AttributeError:
                    pass

        return f(*args, **kwargs)

    return _inner_send_data


def _get_db_data(connection: clickhouse_driver.connection.Connection) -> dict[str, str]:
    return {
        SPANDATA.DB_SYSTEM: "clickhouse",
        SPANDATA.SERVER_ADDRESS: connection.host,
        SPANDATA.SERVER_PORT: connection.port,
        SPANDATA.DB_NAME: connection.database,
        SPANDATA.DB_USER: connection.user,
    }


def _set_on_span(span: Span, data: dict[str, Any]):
    for key, value in data.items():
        span.set_attribute(key, _serialize_span_attribute(value))
