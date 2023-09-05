from importlib import import_module

from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.utils import parse_version
from sentry_sdk._types import TYPE_CHECKING

try:
    from graphql import version as GRAPHQL_CORE_VERSION
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
    from ariadne.types import ContextValue, GraphQLError, GraphQLResult, GraphQLSchema
    from sentry_sdk._types import EventProcessor


class AriadneIntegration(Integration):
    identifier = "ariadne"

    # XXX add an option to turn on error monitoring so that people who are instrumenting
    # both the server and the client side can turn one of them off if they want
    # XXX double patching

    @staticmethod
    def setup_once():
        # type: () -> None
        # XXX version guard for ariadne
        version = parse_version(GRAPHQL_CORE_VERSION)

        if version is None:
            raise DidNotEnable(
                "Unparsable graphql-core version: {}".format(GRAPHQL_CORE_VERSION)
            )

        if version < (3, 2):
            raise DidNotEnable("graphql-core 3.2 or newer required.")

        _patch_graphql()
        _inject_tracing_extension()


def _patch_graphql():
    # type: () -> None
    old_graphql_sync = ariadne_graphql.graphql_sync
    old_graphql_async = ariadne_graphql.graphql

    def _sentry_patched_graphql_sync(schema, data, *args, **kwargs):
        # type: (GraphQLSchema, Any, Any, Any) -> GraphQLResult
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)

        if integration is None or hub.client is None:
            return old_graphql_sync(schema, data, *args, **kwargs)

        scope = hub.scope  # XXX
        if scope is not None:
            event_processor = _make_event_processor(schema, data)
            scope.add_event_processor(event_processor)

        result = old_graphql_sync(schema, data, *args, **kwargs)

        _capture_graphql_errors(result, hub)

        return result

    async def _sentry_patched_graphql_async(schema, data, *args, **kwargs):
        # type: (GraphQLSchema, Any, Any, Any) -> GraphQLResult
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)

        if integration is None or hub.client is None:
            return await old_graphql_async(schema, data, *args, **kwargs)

        scope = hub.scope  # XXX
        if scope is not None:
            event_processor = _make_event_processor(schema, data)
            scope.add_event_processor(event_processor)

        result = await old_graphql_async(schema, data, *args, **kwargs)
        # XXX no result available in event processor like this

        # XXX two options.
        # a) Handle the exceptions in has_errors. Then it's actual exceptions that
        #    can be raised properly. But there is no response and it'd have to be
        #    constructed manually
        # b) Handle the exceptions here once they've already been formatted. Then
        #    we need to reconstruct the exceptions instead before raising them.
        # c?) Remember the exception somehow
        # d) monkeypatch handle_graphql_errors

        _capture_graphql_errors(result, hub)

        return result

    ariadne_graphql.graphql_sync = _sentry_patched_graphql_sync
    ariadne_graphql.graphql = _sentry_patched_graphql_async
    ariadne_http.graphql = _sentry_patched_graphql_async


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
        # XXX could use this to capture exceptions but we don't have access
        # to the response here
        print("has errors ctx", context)
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)
        if integration is None or hub.client is None:
            return

    # XXX def resolve()


def _capture_graphql_errors(result, hub):
    result = result[1]
    if not isinstance(result, dict):
        return

    for error in result.get("errors") or []:
        # have to reconstruct the exceptions here since they've already been
        # formatted
        hub.capture_exception(error)


def _make_event_processor(schema, data):
    # type: (GraphQLSchema, Any) -> EventProcessor
    def inner(event, hint):
        print("in event processor")
        print(schema)
        print(data)
        if _should_send_default_pii():
            if isinstance(data, dict) and data.get("query"):
                request_info = event.setdefault("request", {})
                request_info["api_target"] = "graphql"
                request_info["data"] = str(data["query"])

        return event

    return inner
