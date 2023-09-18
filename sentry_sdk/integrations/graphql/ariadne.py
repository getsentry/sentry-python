from importlib import import_module
from inspect import iscoroutinefunction

from sentry_sdk.consts import OP
from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations.graphql._common import (
    OPERATION_TYPE_TO_OP,
    get_operation_type_and_name_from_operation_definition,
)
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
    parse_version,
)
from sentry_sdk._types import TYPE_CHECKING

try:
    from graphql import version as GRAPHQL_CORE_VERSION
    from graphql.execution.execute import ExecutionContext
    from graphql.language import DocumentNode, OperationDefinitionNode
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
    from typing import Any, Dict, List
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
    # XXX max_request_body_size? see request_body_within_bounds
    # XXX version guard for ariadne
    # XXX if we end up patching graphql-core, we need guards to prevent re-patching
    # XXX subscriptions
    # XXX ws

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


def _patch_graphql():
    # type: () -> None
    old_graphql_sync = ariadne_graphql.graphql_sync
    old_graphql_async = ariadne_graphql.graphql
    old_handle_errors = ariadne_graphql.handle_graphql_errors
    old_handle_query_result = ariadne_graphql.handle_query_result
    old_execute_async = ariadne_graphql.execute
    old_execute_sync = ariadne_graphql.execute_sync
    old_parse_query = ariadne_graphql.parse_query

    def _sentry_patched_graphql_sync(schema, data, *args, **kwargs):
        # type: (GraphQLSchema, Any, Any, Any) -> GraphQLResult
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)
        if integration is None:
            return old_graphql_sync(schema, data, *args, **kwargs)

        scope = hub.scope  # XXX configure?
        if scope is not None:
            event_processor = _make_request_event_processor(schema, data)
            scope.add_event_processor(event_processor)

        result = old_graphql_sync(schema, data, *args, **kwargs)
        return result

    async def _sentry_patched_graphql_async(schema, data, *args, **kwargs):
        # type: (GraphQLSchema, Any, Any, Any) -> GraphQLResult
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)
        if integration is None:
            return await old_graphql_async(schema, data, *args, **kwargs)

        scope = hub.scope  # XXX configure?
        if scope is not None:
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

        scope = hub.scope  # XXX configure?
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

    def _sentry_patched_handle_query_result(result, *args, **kwargs):
        # type: (Any, Any, Any) -> GraphQLResult
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)
        if integration is None:
            return old_handle_query_result(result, *args, **kwargs)

        response = old_handle_query_result(result, *args, **kwargs)

        scope = hub.scope  # XXX configure?
        if scope is not None:
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

    def _sentry_patched_execute_sync(schema, document, *args, **kwargs):
        # XXX
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)
        if integration is None:
            return old_execute_sync(schema, document, *args, **kwargs)

        print(type(document))
        print("execute_sync operation", extract_operation_type_and_name(document))

        result = old_execute_sync(schema, document, *args, **kwargs)

        return result

    def _sentry_patched_execute_async(schema, document, *args, **kwargs):
        # XXX
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)
        if integration is None:
            return old_execute_async(schema, document, *args, **kwargs)

        print(type(document))
        print("execute_async operation", extract_operation_type_and_name(document))

        result = old_execute_async(schema, document, *args, **kwargs)

        return result

    def _sentry_patched_parse_query(*args, **kwargs):
        # XXX
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)
        if integration is None:
            return old_parse_query(*args, **kwargs)

        document = old_parse_query(*args, **kwargs)
        scope = hub.scope  # XXX configure?
        if scope is not None:
            event_processor = _make_operation_event_processor(document)
            scope.add_event_processor(event_processor)

        return document

    ariadne_graphql.graphql_sync = _sentry_patched_graphql_sync
    ariadne_graphql.graphql = _sentry_patched_graphql_async
    ariadne_http.graphql = _sentry_patched_graphql_async
    ariadne_graphql.handle_graphql_errors = _sentry_patched_handle_graphql_errors
    ariadne_graphql.handle_query_result = _sentry_patched_handle_query_result
    # ariadne_graphql.execute = _sentry_patched_execute_async
    # ariadne_graphql.execute_sync = _sentry_patched_execute_sync
    # ariadne_graphql.parse_query = _sentry_patched_parse_query


def _patch_execution_context():
    # type: () -> None
    old_init = ExecutionContext.__init__
    old_execute_operation = ExecutionContext.execute_operation
    old_build_response = ExecutionContext.build_response

    def _sentry_patched_init(
        self, schema, fragments, root_value, context_value, operation, *args, **kwargs
    ):
        print("ExecutionContext init")
        # send_name = getattr(send, "__name__", str(send))
        # send_patched = send_name == "_sentry_send"

        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)
        if integration is None:
            return old_init(
                self,
                schema,
                fragments,
                root_value,
                context_value,
                operation,
                *args,
                **kwargs
            )

        return old_init(
            self,
            schema,
            fragments,
            root_value,
            context_value,
            operation,
            *args,
            **kwargs
        )

    def _sentry_patched_execute_operation(self, *args, **kwargs):
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)
        if integration is None:
            return old_execute_operation(self, *args, **kwargs)
        (
            operation_type,
            operation_name,
        ) = get_operation_type_and_name_from_operation_definition(self.operation)

        scope = hub.scope  # XXX
        if scope is not None:
            if scope.span:
                _sentry_span = scope.span.start_child(
                    op=OPERATION_TYPE_TO_OP.get(operation_type)
                )
            else:
                _sentry_span = hub.start_span(
                    op=OPERATION_TYPE_TO_OP.get(operation_type)
                )

        result = old_execute_operation(self, *args, **kwargs)
        _sentry_span.finish()
        print("span finished")
        return result

    def _sentry_patched_build_response(*args, **kwargs):
        hub = Hub.current
        integration = hub.get_integration(AriadneIntegration)
        if integration is None:
            return old_build_response(*args, **kwargs)

        result = old_build_response(*args, **kwargs)
        # XXX self._sentry_span.finish()
        return result

    ExecutionContext.__init__ = _sentry_patched_init
    ExecutionContext.execute_operation = _sentry_patched_execute_operation
    # XXX ExecutionContext.build_response = _sentry_patched_build_response


def _make_request_event_processor(data):
    # type: (GraphQLSchema) -> EventProcessor
    """Add request data to events."""

    def inner(event, hint):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        if _should_send_default_pii():
            if isinstance(data, dict) and data.get("query"):
                request_info = event.setdefault("request", {})
                request_info["api_target"] = "graphql"
                request_info["data"] = str(data["query"])

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


def _make_operation_event_processor(document_node):
    # type: (DocumentNode) -> EventProcessor
    def inner(event, hint):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        # If the operation type is not known yet, set it to the first operation
        # in the document
        print(document_node.to_dict())
        operation_type, operation_name = extract_operation_type_and_name(document_node)

        if operation_type is not None:
            type_to_op = {
                "query": OP.HTTP_GRAPHQL_QUERY,
                "mutation": OP.HTTP_GRAPHQL_MUTATION,
                "subscription": OP.HTTP_GRAPHQL_SUBSCRIPTION,
            }
            print("OPERATION TYPE", type_to_op.get(operation_type))
        print("OPERATION NAME", operation_name)

        return event

    return inner
