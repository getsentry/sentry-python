from sentry_sdk.hub import Hub
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.utils import parse_version
from sentry_sdk._types import TYPE_CHECKING

try:
    from graphql import version as GRAPHQL_CORE_VERSION
except ImportError:
    raise DidNotEnable("graphql-core not installed")

supported_library_installed = False

try:
    from graphene.types import schema as graphene_schema

    supported_library_installed = True
except ImportError:
    graphene_schema = None


if not supported_library_installed:
    raise DidNotEnable("graphene not installed")  # XXX more server impls


if TYPE_CHECKING:
    pass


class GraphQLServerIntegration(Integration):
    identifier = "graphql_server"

    @staticmethod
    def setup_once():
        # type: () -> None
        global graphene_schema

        version = parse_version(GRAPHQL_CORE_VERSION)

        if version is None:
            raise DidNotEnable(
                "Unparsable graphql-core version: {}".format(GRAPHQL_CORE_VERSION)
            )

        if version < (3, 2):
            raise DidNotEnable("graphql-core 3.2 or newer required.")

        # XXX async
        if graphene_schema is not None:
            old_execute_sync = graphene_schema.graphql_sync

            def sentry_patched_graphql_sync(*args, **kwargs):
                hub = Hub.current
                integration = hub.get_integration(GraphQLServerIntegration)
                if integration is None or hub.client is None:
                    return old_execute_sync(*args, **kwargs)

                result = old_execute_sync(*args, **kwargs)

                for error in result.errors or []:
                    hub.capture_exception(error)

                return result

            graphene_schema.graphql_sync = sentry_patched_graphql_sync
