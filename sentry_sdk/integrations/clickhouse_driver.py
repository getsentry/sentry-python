from __future__ import annotations
import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import _check_minimum_version, Integration, DidNotEnable
from sentry_sdk.tracing import Span
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import (
    _serialize_span_attribute,
    capture_internal_exceptions,
    ensure_integration_enabled,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Any, ParamSpec, Callable, TypeVar

    P = ParamSpec("P")
    T = TypeVar("T")


try:
    import clickhouse_driver  # type: ignore[import-not-found]

except ImportError:
    raise DidNotEnable("clickhouse-driver not installed.")


class ClickhouseDriverIntegration(Integration):
    identifier = "clickhouse_driver"
    origin = f"auto.db.{identifier}"

    @staticmethod
    def setup_once() -> None:
        _check_minimum_version(ClickhouseDriverIntegration, clickhouse_driver.VERSION)

        # Every query is done using the Connection's `send_query` function
        clickhouse_driver.connection.Connection.send_query = _wrap_start(
            clickhouse_driver.connection.Connection.send_query
        )

        # If the query contains parameters then the send_data function is used to send those parameters to clickhouse
        _wrap_send_data()

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
            only_as_child_span=True,
        )

        connection._sentry_span = span  # type: ignore[attr-defined]

        data: dict[str, Any] = _get_db_data(connection)
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

        client = args[0]
        if not isinstance(client, clickhouse_driver.client.Client):
            return res

        connection = client.connection

        span = getattr(connection, "_sentry_span", None)
        if span is not None:
            data = getattr(connection, "_sentry_db_data", {})

            if res is not None and should_send_default_pii():
                data["db.result"] = res
                span.set_attribute("db.result", _serialize_span_attribute(res))

            with capture_internal_exceptions():
                query = data.pop("db.query.text", None)
                if query:
                    sentry_sdk.add_breadcrumb(
                        message=query, category="query", data=data
                    )

            span.finish()

            try:
                del connection._sentry_db_data
                del connection._sentry_span
            except AttributeError:
                pass

        return res

    return _inner_end


def _wrap_send_data() -> None:
    original_send_data = clickhouse_driver.client.Client.send_data

    def _inner_send_data(
        self: clickhouse_driver.client.Client,
        sample_block: Any,
        data: Any,
        types_check: bool = False,
        columnar: bool = False,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        span = getattr(self.connection, "_sentry_span", None)
        if span is None:
            return original_send_data(
                self, sample_block, data, types_check, columnar, *args, **kwargs
            )

        db_data = _get_db_data(self.connection)
        _set_on_span(span, db_data)

        saved_db_data: dict[str, Any] = getattr(self.connection, "_sentry_db_data", {})
        db_params: list[Any] = saved_db_data.get("db.params") or []

        if should_send_default_pii():
            if isinstance(data, (list, tuple)):
                db_params.extend(data)

            else:  # data is a generic iterator
                orig_data = data

                # Wrap the generator to add items to db.params as they are yielded.
                # This allows us to send the params to Sentry without needing to allocate
                # memory for the entire generator at once.
                def wrapped_generator() -> "Iterator[Any]":
                    for item in orig_data:
                        db_params.append(item)
                        yield item

                # Replace the original iterator with the wrapped one.
                data = wrapped_generator()

        rv = original_send_data(
            self, sample_block, data, types_check, columnar, *args, **kwargs
        )

        if should_send_default_pii() and db_params:
            # need to do this after the original function call to make sure
            # db_params is populated correctly
            saved_db_data["db.params"] = db_params
            span.set_attribute("db.params", _serialize_span_attribute(db_params))

        return rv

    clickhouse_driver.client.Client.send_data = _inner_send_data


def _get_db_data(connection: clickhouse_driver.connection.Connection) -> dict[str, str]:
    return {
        SPANDATA.DB_SYSTEM: "clickhouse",
        SPANDATA.SERVER_ADDRESS: connection.host,
        SPANDATA.SERVER_PORT: connection.port,
        SPANDATA.DB_NAME: connection.database,
        SPANDATA.DB_USER: connection.user,
    }


def _set_on_span(span: Span, data: dict[str, Any]) -> None:
    for key, value in data.items():
        span.set_attribute(key, _serialize_span_attribute(value))
