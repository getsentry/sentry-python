from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations.modules import _get_installed_modules
from sentry_sdk.utils import parse_version
from sentry_sdk._types import TYPE_CHECKING

try:
    from graphql import version as GRAPHQL_CORE_VERSION
except ImportError:
    raise DidNotEnable("graphql-core not installed")


try:
    from graphql import schema as graphene_schema
except ImportError:
    raise DidNotEnable("graphene not installed")


if TYPE_CHECKING:
    from typing import Any, Union
    from graphene.language.source import Source
    from graphql.execution import ExecutionResult
    from graphql.type import GraphQLSchema
    from sentry_sdk._types import EventProcessor


class GrapheneIntegration(Integration):
    identifier = "graphene"

    # XXX add an option to turn on error monitoring so that people who are instrumenting
    # both the server and the client side can turn one of them off if they want

    @staticmethod
    def setup_once():
        # type: () -> None
        # XXX version guard for graphene
        version = parse_version(GRAPHQL_CORE_VERSION)

        if version is None:
            raise DidNotEnable(
                "Unparsable graphql-core version: {}".format(GRAPHQL_CORE_VERSION)
            )

        if version < (3, 2):
            raise DidNotEnable("graphql-core 3.2 or newer required.")

        # XXX: a guard against patching multiple times?
        old_graphql_sync = graphene_schema.graphql_sync
        old_graphql_async = graphene_schema.graphql

        def _sentry_patched_graphql_sync(schema, source, *args, **kwargs):
            # type: (GraphQLSchema, Union[str, Source], Any, Any) -> ExecutionResult
            hub = Hub.current
            integration = hub.get_integration(GrapheneIntegration)
            if integration is None or hub.client is None:
                return old_graphql_sync(schema, source, *args, **kwargs)

            scope = hub.scope  # XXX
            if scope is not None:
                event_processor = _make_event_processor(source)
                scope.add_event_processor(event_processor)

            result = old_graphql_sync(schema, source, *args, **kwargs)

            _raise_graphql_errors(result)

            return result

        async def _sentry_patched_graphql_async(schema, source, *args, **kwargs):
            # type: (GraphQLSchema, Union[str, Source], Any, Any) -> ExecutionResult
            hub = Hub.current
            integration = hub.get_integration(GrapheneIntegration)
            if integration is None or hub.client is None:
                return await old_graphql_async(schema, source, *args, **kwargs)

            scope = hub.scope  # XXX
            if scope is not None:
                event_processor = _make_event_processor(source)
                scope.add_event_processor(event_processor)

            result = await old_graphql_async(schema, source, *args, **kwargs)

            _raise_graphql_errors(result)

            return result

        graphene_schema.graphql_sync = _sentry_patched_graphql_sync
        graphene_schema.graphql = _sentry_patched_graphql_async


def _raise_graphql_errors(result, hub):
    # type: (ExecutionResult, Hub) -> None
    for error in result.errors or []:
        hub.capture_exception(error)


def _make_event_processor(source):
    # type: (Source) -> EventProcessor
    def inner(event, hint):
        if _should_send_default_pii():
            request_info = event.setdefault("request", {})
            request_info["data"] = str(source)

        return event

    return inner
