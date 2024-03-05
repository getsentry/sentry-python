from contextlib import contextmanager

from sentry_sdk import start_span
from sentry_sdk.consts import OP
from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
    package_version,
)
from sentry_sdk._types import TYPE_CHECKING


try:
    from graphene.types import schema as graphene_schema  # type: ignore
except ImportError:
    raise DidNotEnable("graphene is not installed")


if TYPE_CHECKING:
    from typing import Any, Dict, Union
    from graphene.language.source import Source  # type: ignore
    from graphql.execution import ExecutionResult  # type: ignore
    from graphql.type import GraphQLSchema  # type: ignore


class GrapheneIntegration(Integration):
    identifier = "graphene"

    @staticmethod
    def setup_once():
        # type: () -> None
        version = package_version("graphene")

        if version is None:
            raise DidNotEnable("Unparsable graphene version.")

        if version < (3, 3):
            raise DidNotEnable("graphene 3.3 or newer required.")

        _patch_graphql()


def _patch_graphql():
    # type: () -> None
    old_graphql_sync = graphene_schema.graphql_sync
    old_graphql_async = graphene_schema.graphql

    def _sentry_patched_graphql_sync(schema, source, *args, **kwargs):
        # type: (GraphQLSchema, Union[str, Source], Any, Any) -> ExecutionResult
        hub = Hub.current
        integration = hub.get_integration(GrapheneIntegration)
        if integration is None:
            return old_graphql_sync(schema, source, *args, **kwargs)

        with graphql_span(hub, schema, source, kwargs):
            result = old_graphql_sync(schema, source, *args, **kwargs)

        with capture_internal_exceptions():
            for error in result.errors or []:
                event, hint = event_from_exception(
                    error,
                    client_options=hub.client.options if hub.client else None,
                    mechanism={
                        "type": integration.identifier,
                        "handled": False,
                    },
                )
                hub.capture_event(event, hint=hint)

        return result

    async def _sentry_patched_graphql_async(schema, source, *args, **kwargs):
        # type: (GraphQLSchema, Union[str, Source], Any, Any) -> ExecutionResult
        hub = Hub.current
        integration = hub.get_integration(GrapheneIntegration)
        if integration is None:
            return await old_graphql_async(schema, source, *args, **kwargs)

        with graphql_span(hub, schema, source, kwargs):
            result = await old_graphql_async(schema, source, *args, **kwargs)

        with capture_internal_exceptions():
            for error in result.errors or []:
                event, hint = event_from_exception(
                    error,
                    client_options=hub.client.options if hub.client else None,
                    mechanism={
                        "type": integration.identifier,
                        "handled": False,
                    },
                )
                hub.capture_event(event, hint=hint)

        return result

    graphene_schema.graphql_sync = _sentry_patched_graphql_sync
    graphene_schema.graphql = _sentry_patched_graphql_async


def _event_processor(event, hint):
    # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
    if _should_send_default_pii():
        request_info = event.setdefault("request", {})
        request_info["api_target"] = "graphql"

    elif event.get("request", {}).get("data"):
        del event["request"]["data"]

    return event


@contextmanager
def graphql_span(hub, schema, source, kwargs):
    operation_name = kwargs.get("operation_name")

    operation_type = "query"
    op = OP.GRAPHQL_QUERY
    if source.strip().startswith("mutation"):
        operation_type = "mutation"
        op = OP.GRAPHQL_MUTATION
    elif source.strip().startswith("subscription"):
        operation_type = "subscription"
        op = OP.GRAPHQL_SUBSCRIPTION

    hub.add_breadcrumb(
        crumb={
            "data": {
                "operation_name": operation_name,
                "operation_type": operation_type,
            },
            "category": "graphql.operation",
        },
    )

    with hub.configure_scope() as scope:
        if scope.span:
            _graphql_span = scope.span.start_child(op=op, description=operation_name)
        else:
            _graphql_span = start_span(op=op, description=operation_name)
        scope.add_event_processor(_event_processor)

    _graphql_span.set_data("graphql.document", source)
    _graphql_span.set_data("graphql.operation.name", operation_name)
    _graphql_span.set_data("graphql.operation.type", operation_type)

    yield

    _graphql_span.finish()
