from importlib import import_module

from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations.modules import _get_installed_modules
from sentry_sdk.utils import event_from_exception, parse_version
from sentry_sdk._types import TYPE_CHECKING

try:
    ariadne_graphql = import_module(
        "ariadne.graphql"
    )  # necessary because of some name shadowing shenanigans in ariadne
    from ariadne.asgi.handlers import http as ariadne_http
except ImportError:
    raise DidNotEnable("ariadne not installed")


if TYPE_CHECKING:
    from typing import Any, Dict, List
    from ariadne.types import GraphQLError, GraphQLResult, GraphQLSchema
    from sentry_sdk._types import EventProcessor


class AriadneIntegration(Integration):
    identifier = "ariadne"

    # XXX double patching
    # XXX if we end up patching graphql-core, we need guards to prevent re-patching
    # XXX capture internal exceptions
    # XXX capture request/response? to override pii?
    # XXX max_request_body_size? see request_body_within_bounds
    # XXX subscriptions
    # XXX ws

    @staticmethod
    def setup_once():
        # type: () -> None
        installed_packages = _get_installed_modules()
        version = parse_version(installed_packages["ariadne"])

        if version is None:
            raise DidNotEnable("Unparsable ariadne version: {}".format(version))

        if version < (0, 20):
            raise DidNotEnable("ariadne 0.20 or newer required.")

        _patch_graphql()


def _patch_graphql():
    # type: () -> None
    old_graphql_sync = ariadne_graphql.graphql_sync
    old_graphql_async = ariadne_graphql.graphql
    old_handle_errors = ariadne_graphql.handle_graphql_errors
    old_handle_query_result = ariadne_graphql.handle_query_result

    def _sentry_patched_graphql_sync(schema, data, *args, **kwargs):
        # type: (GraphQLSchema, Any, Any, Any) -> GraphQLResult
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)
        if integration is None:
            return old_graphql_sync(schema, data, *args, **kwargs)

        with hub.configure_scope() as scope:
            event_processor = _make_request_event_processor(data)
            scope.add_event_processor(event_processor)

        result = old_graphql_sync(schema, data, *args, **kwargs)
        return result

    async def _sentry_patched_graphql_async(schema, data, *args, **kwargs):
        # type: (GraphQLSchema, Any, Any, Any) -> GraphQLResult
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)
        if integration is None:
            return await old_graphql_async(schema, data, *args, **kwargs)

        with hub.configure_scope() as scope:
            event_processor = _make_request_event_processor(data)
            scope.add_event_processor(event_processor)

        result = await old_graphql_async(schema, data, *args, **kwargs)
        return result

    def _sentry_patched_handle_graphql_errors(errors, *args, **kwargs):
        # type: (List[GraphQLError], Any, Any) -> GraphQLResult
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)
        if integration is None:
            return old_handle_errors(errors, *args, **kwargs)

        result = old_handle_errors(errors, *args, **kwargs)

        with hub.configure_scope() as scope:
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

    def _sentry_patched_handle_query_result(result, *args, **kwargs):
        # type: (Any, Any, Any) -> GraphQLResult
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)
        if integration is None:
            return old_handle_query_result(result, *args, **kwargs)

        response = old_handle_query_result(result, *args, **kwargs)

        with hub.configure_scope() as scope:
            event_processor = _make_response_event_processor(response[1])
            scope.add_event_processor(event_processor)

        for error in result.errors or []:
            event, hint = event_from_exception(
                error,
                client_options=hub.client.options,
                mechanism={
                    "type": hub.get_integration(AriadneIntegration).identifier,
                    "handled": False,
                },
            )
            hub.capture_event(event, hint=hint)

        return response

    ariadne_graphql.graphql_sync = _sentry_patched_graphql_sync
    ariadne_graphql.graphql = _sentry_patched_graphql_async
    ariadne_http.graphql = _sentry_patched_graphql_async
    ariadne_graphql.handle_graphql_errors = _sentry_patched_handle_graphql_errors
    ariadne_graphql.handle_query_result = _sentry_patched_handle_query_result


def _make_request_event_processor(data):
    # type: (GraphQLSchema) -> EventProcessor
    """Add request data to events."""

    def inner(event, hint):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        if _should_send_default_pii() and isinstance(data, dict):
            request_info = event.setdefault("request", {})
            request_info["api_target"] = "graphql"
            request_info["data"] = data

        return event

    return inner


def _make_response_event_processor(response):
    # type: (dict) -> EventProcessor
    """Add response data to the event's response context."""

    def inner(event, hint):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        if _should_send_default_pii() and response.get("errors"):
            request_info = event.setdefault("contexts", {})
            request_info["response"] = {
                "data": response,
            }

        return event

    return inner
