from importlib import import_module
from inspect import iscoroutinefunction

from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.utils import event_from_exception, parse_version
from sentry_sdk._types import TYPE_CHECKING

try:
    from graphql import version as GRAPHQL_CORE_VERSION
    from graphql.execution.execute import ExecutionContext
    from graphql.pyutils import is_awaitable
except ImportError:
    raise DidNotEnable("graphql-core not installed")

try:
    ariadne_graphql = import_module(
        "ariadne.graphql"
    )  # necessary because of some name shadowing shenanigans in ariadne
    # XXX verify again
    from ariadne.asgi import GraphQL as ASGIGraphQL
    from ariadne.asgi.handlers import GraphQLHTTPHandler
    from ariadne.asgi.handlers import http as ariadne_http
    from ariadne.types import Extension
    from ariadne.wsgi import GraphQL as WSGIGraphQL
except ImportError:
    raise DidNotEnable("ariadne not installed")


if TYPE_CHECKING:
    from typing import Any, List
    from ariadne.types import (
        ContextValue,
        GraphQLError,
        GraphQLResult,
        GraphQLSchema,
        Resolver,
    )
    from graphql import GraphQLResolveInfo
    from sentry_sdk._types import EventProcessor


class AriadneIntegration(Integration):
    identifier = "ariadne"

    # XXX add an option to turn on error monitoring so that people who are instrumenting
    # both the server and the client side can turn one of them off if they want
    # XXX double patching
    # XXX capture internal exceptions
    # XXX capture request/response? to override pii?
    # XXX max_request_body_size?
    # XXX version guard for ariadne
    # XXX if we end up patching graphql-core, we need guards

    @staticmethod
    def setup_once():
        # type: () -> None
        version = parse_version(GRAPHQL_CORE_VERSION)

        if version is None:
            raise DidNotEnable(
                "Unparsable graphql-core version: {}".format(GRAPHQL_CORE_VERSION)
            )

        if version < (3, 2):
            raise DidNotEnable("graphql-core 3.2 or newer required.")

        _patch_graphql()
        _patch_execution_context()
        _inject_tracing_extension()


def _patch_graphql():
    # type: () -> None
    old_graphql_sync = ariadne_graphql.graphql_sync
    old_graphql_async = ariadne_graphql.graphql
    old_handle_errors = ariadne_graphql.handle_graphql_errors

    def _sentry_patched_graphql_sync(schema, data, *args, **kwargs):
        # type: (GraphQLSchema, Any, Any, Any) -> GraphQLResult
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)

        if integration is None or hub.client is None:
            return old_graphql_sync(schema, data, *args, **kwargs)

        scope = hub.scope  # XXX
        if scope is not None:
            event_processor = _make_request_event_processor(schema, data)
            scope.add_event_processor(event_processor)

        result = old_graphql_sync(schema, data, *args, **kwargs)
        return result

    async def _sentry_patched_graphql_async(schema, data, *args, **kwargs):
        # type: (GraphQLSchema, Any, Any, Any) -> GraphQLResult
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)

        if integration is None or hub.client is None:
            return await old_graphql_async(schema, data, *args, **kwargs)

        scope = hub.scope  # XXX
        if scope is not None:
            event_processor = _make_request_event_processor(schema, data)
            scope.add_event_processor(event_processor)

        result = await old_graphql_async(schema, data, *args, **kwargs)
        return result

    def _sentry_patched_handle_graphql_errors(errors, *args, **kwargs):
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)

        if integration is None or hub.client is None:
            return old_handle_errors(errors, *args, **kwargs)

        result = old_handle_errors(errors, *args, **kwargs)

        scope = hub.scope  # XXX
        if scope is not None:
            event_processor = _make_response_event_processor(result[1])
            scope.add_event_processor(event_processor)

        for error in errors:
            event, hint = event_from_exception(
                error,
                client_options=hub.client.options,
                mechanism={
                    "type": hub.get_integration(AriadneIntegration).identifier,
                    "handled": False,
                },
            )
            hub.capture_event(event, hint=hint)

        return result

    ariadne_graphql.graphql_sync = _sentry_patched_graphql_sync
    ariadne_graphql.graphql = _sentry_patched_graphql_async
    ariadne_http.graphql = _sentry_patched_graphql_async
    ariadne_graphql.handle_graphql_errors = _sentry_patched_handle_graphql_errors


def _patch_execution_context():
    # type: () -> None
    old_execution_context_init = ExecutionContext.__init__

    def _sentry_execution_context_init_wrapper(self, *args, **kwargs):
        print("exec context init")

        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)
        if integration is None:
            return old_execution_context_init(self, *args, **kwargs)

        return old_execution_context_init(self, *args, **kwargs)

    ExecutionContext.__init__ = _sentry_execution_context_init_wrapper


def _inject_tracing_extension():
    # type: () -> None
    old_asgi_http_handler_init = GraphQLHTTPHandler.__init__
    old_asgi_init = ASGIGraphQL.__init__
    old_wsgi_init = WSGIGraphQL.__init__

    def _sentry_asgi_http_handler_wrapper(self, *args, **kwargs):
        # type: (GraphQLHTTPHandler, Any, Any) -> None
        extensions = kwargs.get("extensions") or []
        extensions.append(SentryTracingExtension)
        kwargs["extensions"] = extensions
        old_asgi_http_handler_init(self, *args, **kwargs)

    def _sentry_asgi_wrapper(self, *args, **kwargs):
        # type: (ASGIGraphQL, Any, Any) -> None
        http_handler = kwargs.get("http_handler")
        if http_handler is None:
            http_handler = GraphQLHTTPHandler()
        kwargs["http_handler"] = http_handler
        old_asgi_init(self, *args, **kwargs)

    def _sentry_wsgi_wrapper(self, *args, **kwargs):
        # type: (WSGIGraphQL, Any, Any) -> None
        extensions = kwargs.get("extensions") or []
        extensions.append(SentryTracingExtension)
        kwargs["extensions"] = extensions
        old_wsgi_init(self, *args, **kwargs)

    GraphQLHTTPHandler.__init__ = _sentry_asgi_http_handler_wrapper
    ASGIGraphQL.__init__ = _sentry_asgi_wrapper
    WSGIGraphQL.__init__ = _sentry_wsgi_wrapper


class SentryTracingExtension(Extension):
    # XXX this probably doesn't have enough granularity
    def __init__(self):
        pass

    def request_started(self, context):
        # type: (ContextValue) -> None
        print("req started ctx", context)
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)
        if integration is None:
            return

    def request_finished(self, context):
        # type: (ContextValue) -> None
        print("req finished ctx", context)
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)
        if integration is None or hub.client is None:
            return

    def has_errors(self, errors, context):
        # type: (List[GraphQLError], ContextValue) -> None
        print("has errors ctx", context)
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)
        if integration is None or hub.client is None:
            return

    def resolve(self, next_, obj, info, **kwargs):
        # type: (Resolver, Any, GraphQLResolveInfo, Any) -> Any
        # Fast implementation for synchronous resolvers
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)
        if integration is not None:
            hub.add_breadcrumb(
                type="graphql",
                category="graphql.fetcher",
                data={
                    "operation_name": "",
                    "operation_type": "",
                    "operation_id": "",
                },
            )

        if not iscoroutinefunction(next_):
            result = next_(obj, info, **kwargs)
            return result

        # Create async closure for async `next_` that GraphQL
        # query executor will handle for us.
        async def async_my_extension():
            result = await next_(obj, info, **kwargs)
            if is_awaitable(result):
                result = await result
            return result

        # GraphQL query executor will execute this closure for us
        return async_my_extension()


def _make_request_event_processor(schema, data):
    # type: (GraphQLSchema, Any) -> EventProcessor
    def inner(event, hint):
        if _should_send_default_pii():
            if isinstance(data, dict) and data.get("query"):
                request_info = event.setdefault("request", {})
                request_info["api_target"] = "graphql"
                request_info["data"] = str(data["query"])

        return event

    return inner


def _make_response_event_processor(response):
    # type: (dict) -> EventProcessor
    def inner(event, hint):
        if _should_send_default_pii():
            request_info = event.setdefault("contexts", {})
            request_info["response"] = {
                "data": response,
            }

        return event

    return inner
