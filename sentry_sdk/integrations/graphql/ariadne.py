from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.utils import parse_version
from sentry_sdk._types import TYPE_CHECKING

try:
    from graphql import version as GRAPHQL_CORE_VERSION
except ImportError:
    raise DidNotEnable("graphql-core not installed")

try:
    import ariadne
except ImportError:
    raise DidNotEnable("ariadne not installed")


if TYPE_CHECKING:
    from typing import Any
    from ariadne.graphql import GraphQLSchema
    from ariadne.types import GraphQLResult
    from sentry_sdk._types import EventProcessor


class AriadneIntegration(Integration):
    identifier = "ariadne"

    @staticmethod
    def setup_once():
        # type: () -> None
        # XXX version guard for ariadne
        # guard for double patching
        version = parse_version(GRAPHQL_CORE_VERSION)

        if version is None:
            raise DidNotEnable(
                "Unparsable graphql-core version: {}".format(GRAPHQL_CORE_VERSION)
            )

        if version < (3, 2):
            raise DidNotEnable("graphql-core 3.2 or newer required.")

        old_graphql_sync = ariadne.graphql_sync
        old_graphql_async = ariadne.graphql

        def _sentry_patched_graphql_sync(schema, data, *args, **kwargs):
            # type: (GraphQLSchema, Any, Any, Any) -> GraphQLResult
            hub = Hub.current
            integration = hub.get_integration(AriadneIntegration)
            if integration is None or hub.client is None:
                return old_graphql_sync(schema, data, *args, **kwargs)

            result = old_graphql_sync(schema, data, *args, **kwargs)

            _raise_errors(result, hub)

            return result

        async def _sentry_patched_graphql_async(schema, data, *args, **kwargs):
            # type: (GraphQLSchema, Any, Any, Any) -> GraphQLResult
            hub = Hub.current
            integration = hub.get_integration(AriadneIntegration)
            if integration is None or hub.client is None:
                return await old_graphql_async(schema, data, *args, **kwargs)

            result = await old_graphql_async(schema, data, *args, **kwargs)

            _raise_errors(result, hub)

            return result

        ariadne.graphql_sync = _sentry_patched_graphql_sync
        ariadne.graphql_async = _sentry_patched_graphql_async


def _raise_errors(result, hub):
    # type: (GraphQLResult, Hub) -> None
    data = result[1]
    if not isinstance(data, dict) or not data.get("errors"):
        return result

    for error in data["errors"]:
        hub.capture_exception(error)


def _make_event_processor():
    # type: () -> EventProcessor
    def inner(event, hint):
        if _should_send_default_pii():
            pass

        return event

    return inner
