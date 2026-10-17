import functools
import json
from contextlib import contextmanager
from typing import TYPE_CHECKING, TypeVar

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.utils import capture_internal_exceptions

# Hack to get new Python features working in older versions
# without introducing a hard dependency on `typing_extensions`
# from: https://stackoverflow.com/a/71944042/300572
if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Any, Awaitable, Callable, Mapping, Optional, ParamSpec, Union

    from sentry_sdk.tracing import Span
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
    from elasticsearch import (  # type: ignore[import-not-found]
        __version__ as ES_VERSION,
    )
    from elasticsearch._sync.client._base import (  # type: ignore[import-not-found]
        BaseClient,
    )
except ImportError:
    raise DidNotEnable("elasticsearch not installed.")

try:
    from elasticsearch._async.client._base import (  # type: ignore[import-not-found]
        BaseClient as AsyncBaseClient,
    )
except ImportError:
    # The async client's transport has optional dependencies. Missing them
    # should not disable instrumentation of the sync client.
    AsyncBaseClient = None


class ElasticsearchIntegration(Integration):
    identifier = "elasticsearch"
    origin = f"auto.db.{identifier}"

    @staticmethod
    def setup_once() -> None:
        _check_minimum_version(ElasticsearchIntegration, ES_VERSION)

        # Every API method of both the sync and the async client funnels its
        # HTTP call through its class's `perform_request`, so patching the two
        # of them covers the whole API surface, including raw
        # `client.perform_request()` calls.
        BaseClient.perform_request = _wrap_perform_request(BaseClient.perform_request)
        if AsyncBaseClient is not None:
            AsyncBaseClient.perform_request = _wrap_perform_request_async(
                AsyncBaseClient.perform_request
            )


P = ParamSpec("P")
T = TypeVar("T")


def _wrap_perform_request(f: "Callable[P, T]") -> "Callable[P, T]":
    @functools.wraps(f)
    def _inner(*args: "P.args", **kwargs: "P.kwargs") -> "T":
        client = sentry_sdk.get_client()
        if client.get_integration(ElasticsearchIntegration) is None:
            return f(*args, **kwargs)

        with _es_span(client, args, kwargs):
            return f(*args, **kwargs)

    return _inner


def _wrap_perform_request_async(
    f: "Callable[P, Awaitable[T]]",
) -> "Callable[P, Awaitable[T]]":
    @functools.wraps(f)
    async def _inner(*args: "P.args", **kwargs: "P.kwargs") -> "T":
        client = sentry_sdk.get_client()
        if client.get_integration(ElasticsearchIntegration) is None:
            return await f(*args, **kwargs)

        with _es_span(client, args, kwargs):
            return await f(*args, **kwargs)

    return _inner


@contextmanager
def _es_span(
    client: "sentry_sdk.client.BaseClient", args: "Any", kwargs: "Any"
) -> "Iterator[None]":
    instance = args[0]
    method = args[1] if len(args) > 1 else kwargs.get("method")
    path = args[2] if len(args) > 2 else kwargs.get("path")
    # `endpoint_id` (e.g. "search", "indices.refresh") and `path_parts` (e.g.
    # {"index": "my-index"}) are only passed by elasticsearch >= 8.12.
    endpoint_id = kwargs.get("endpoint_id")
    path_parts = kwargs.get("path_parts")

    name = str(endpoint_id) if endpoint_id else "{} {}".format(method, path)

    if has_span_streaming_enabled(client.options):
        span = sentry_sdk.traces.start_span(
            name=name,
            attributes={
                "sentry.op": OP.DB,
                "sentry.origin": ElasticsearchIntegration.origin,
                SPANDATA.DB_SYSTEM_NAME: "elasticsearch",
            },
        )
        _set_db_data(span, instance, method, path, endpoint_id, path_parts, kwargs)
        try:
            yield
        finally:
            span.end()
    else:
        with sentry_sdk.start_span(
            op=OP.DB,
            name=name,
            origin=ElasticsearchIntegration.origin,
        ) as span:
            span.set_data(SPANDATA.DB_SYSTEM, "elasticsearch")
            _set_db_data(span, instance, method, path, endpoint_id, path_parts, kwargs)

            try:
                yield
            finally:
                with capture_internal_exceptions():
                    if span.scope is not None:
                        span.scope.add_breadcrumb(
                            message=name, category="query", data=dict(span._data)
                        )


def _set_db_data(
    span: "Union[Span, StreamedSpan]",
    instance: "Any",
    method: "Optional[str]",
    path: "Optional[str]",
    endpoint_id: "Optional[str]",
    path_parts: "Optional[Mapping[str, Any]]",
    kwargs: "Any",
) -> None:
    if isinstance(span, StreamedSpan):
        set_on_span = span.set_attribute
        db_operation_key = SPANDATA.DB_OPERATION_NAME
    else:
        set_on_span = span.set_data
        db_operation_key = SPANDATA.DB_OPERATION

    if endpoint_id:
        set_on_span(db_operation_key, str(endpoint_id))

    if method:
        set_on_span("http.request.method", str(method))

    if path:
        set_on_span("url.path", str(path))

    if path_parts:
        with capture_internal_exceptions():
            for key, value in path_parts.items():
                set_on_span("db.elasticsearch.path_parts.{}".format(key), str(value))

    with capture_internal_exceptions():
        # The client only knows which node served a request at the transport
        # layer, so only report the server when there is no ambiguity.
        nodes = list(instance.transport.node_pool.all())
        if len(nodes) == 1:
            set_on_span(SPANDATA.SERVER_ADDRESS, nodes[0].config.host)
            set_on_span(SPANDATA.SERVER_PORT, nodes[0].config.port)

    body = kwargs.get("body")
    if body is not None and should_send_default_pii():
        if isinstance(span, StreamedSpan):
            with capture_internal_exceptions():
                set_on_span(SPANDATA.DB_QUERY_TEXT, json.dumps(body, default=str))
        else:
            set_on_span(SPANDATA.DB_QUERY_TEXT, body)
