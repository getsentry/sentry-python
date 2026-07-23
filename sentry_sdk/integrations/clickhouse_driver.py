import functools
from typing import TYPE_CHECKING, TypeVar

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing import Span
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.utils import capture_internal_exceptions

# Hack to get new Python features working in older versions
# without introducing a hard dependency on `typing_extensions`
# from: https://stackoverflow.com/a/71944042/300572
if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Any, Callable, ParamSpec, Union
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
    raise DidNotEnable("clickhouse-driver not installed.")


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

        connection = args[0]
        query = args[1]
        query_id = args[2] if len(args) > 2 else kwargs.get("query_id")
        params = args[3] if len(args) > 3 else kwargs.get("params")

        if has_span_streaming_enabled(client.options):
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
        else:
            span = sentry_sdk.start_span(
                op=OP.DB,
                name=query,
                origin=ClickhouseDriverIntegration.origin,
            )

            span.set_data("query", query)

            if query_id:
                span.set_data("db.query_id", query_id)

            if params and should_send_default_pii():
                span.set_data("db.params", params)

        connection._sentry_span = span  # type: ignore[attr-defined]

        if span is not None:
            _set_db_data(span, connection)

        # run the original code
        ret = f(*args, **kwargs)

        return ret

    return _inner


def _wrap_end(f: "Callable[P, T]") -> "Callable[P, T]":
    def _inner_end(*args: "P.args", **kwargs: "P.kwargs") -> "T":
        res = f(*args, **kwargs)
        instance = args[0]
        span = getattr(instance.connection, "_sentry_span", None)  # type: ignore[attr-defined]

        if span is None:
            return res

        if isinstance(span, StreamedSpan):
            span.end()
        else:
            if res is not None and should_send_default_pii():
                span.set_data("db.result", res)

            with capture_internal_exceptions():
                span.scope.add_breadcrumb(
                    message=span._data.pop("query"), category="query", data=span._data
                )

            span.finish()

        return res

    return _inner_end


def _wrap_send_data() -> None:
    original_send_data = Client.send_data

    def _inner_send_data(  # type: ignore[no-untyped-def] # clickhouse-driver does not type send_data
        self, sample_block, data, types_check=False, columnar=False, *args, **kwargs
    ):
        span = getattr(self.connection, "_sentry_span", None)

        if isinstance(span, StreamedSpan):
            _set_db_data(span, self.connection)
            return original_send_data(
                self, sample_block, data, types_check, columnar, *args, **kwargs
            )

        if span is not None:
            _set_db_data(span, self.connection)

            if should_send_default_pii():
                db_params = span._data.get("db.params", [])

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

                span.set_data("db.params", db_params)

        return original_send_data(
            self, sample_block, data, types_check, columnar, *args, **kwargs
        )

    Client.send_data = _inner_send_data


def _set_db_data(span: "Union[Span, StreamedSpan]", connection: "Connection") -> None:
    if isinstance(span, StreamedSpan):
        span.set_attribute(SPANDATA.DB_SYSTEM_NAME, "clickhouse")
        span.set_attribute(SPANDATA.DB_NAMESPACE, connection.database)

        set_on_span = span.set_attribute
    else:
        span.set_data(SPANDATA.DB_SYSTEM, "clickhouse")
        span.set_data(SPANDATA.DB_NAME, connection.database)

        set_on_span = span.set_data

    set_on_span(SPANDATA.DB_DRIVER_NAME, "clickhouse-driver")
    set_on_span(SPANDATA.SERVER_ADDRESS, connection.host)
    set_on_span(SPANDATA.SERVER_PORT, connection.port)
    set_on_span(SPANDATA.DB_USER, connection.user)
