from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations.modules import _get_installed_modules
from sentry_sdk.utils import event_from_exception, parse_version
from sentry_sdk._types import TYPE_CHECKING

try:
    from graphql import schema as graphene_schema
except ImportError:
    raise DidNotEnable("graphql-core is not installed")


if TYPE_CHECKING:
    from typing import Any, Union
    from graphene.language.source import Source
    from graphql.execution import ExecutionResult
    from graphql.type import GraphQLSchema
    from sentry_sdk._types import EventProcessor


class GrapheneIntegration(Integration):
    # XXX guard against double patching
    # XXX maybe an opt-in for capturing request bodies
    identifier = "graphene"

    @staticmethod
    def setup_once():
        # type: () -> None
        installed_packages = _get_installed_modules()
        version = parse_version(installed_packages["graphene"])

        if version is None:
            raise DidNotEnable("Unparsable graphene version: {}".format(version))

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
        if integration is None or hub.client is None:
            return old_graphql_sync(schema, source, *args, **kwargs)

        with hub.configure_scope() as scope:
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

        with hub.configure_scope() as scope:
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
        event, hint = event_from_exception(
            error,
            client_options=hub.client.options,
            mechanism={
                "type": hub.get_integration(GrapheneIntegration).identifier,
                "handled": False,
            },
        )
        hub.capture_event(event, hint=hint)


def _make_event_processor(source):
    # type: (Source) -> EventProcessor
    def inner(event, hint):
        if _should_send_default_pii():
            request_info = event.setdefault("request", {})
            request_info["data"] = str(source)

        return event

    return inner
