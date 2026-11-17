import sentry_sdk
from sentry_sdk.data_collection import (
    _apply_data_collection_filtering_to_query_string,
)
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import (
    capture_internal_exceptions,
    ensure_integration_enabled,
    event_from_exception,
    has_data_collection_enabled,
    parse_url,
    parse_version,
)

try:
    import gql  # type: ignore[import-not-found]
    from gql.transport import (  # type: ignore[import-not-found]
        AsyncTransport,
        Transport,
    )
    from gql.transport.exceptions import (  # type: ignore[import-not-found]
        TransportQueryError,
    )
    from graphql import (
        DocumentNode,
        VariableDefinitionNode,
        get_operation_ast,
        print_ast,
    )

    try:
        # gql 4.0+
        from gql import GraphQLRequest
    except ImportError:
        GraphQLRequest = None

except ImportError:
    raise DidNotEnable("gql is not installed")

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Dict, Optional, Tuple, Union

    from sentry_sdk._types import Event, EventProcessor

    EventDataType = Union[
        Dict[str, Union[str, Tuple[VariableDefinitionNode, ...], None]]
    ]


class GQLIntegration(Integration):
    identifier = "gql"

    @staticmethod
    def setup_once() -> None:
        gql_version = parse_version(gql.__version__)
        _check_minimum_version(GQLIntegration, gql_version)

        _patch_execute()


def _data_from_document(document: "DocumentNode") -> "EventDataType":
    client_options = sentry_sdk.get_client().options
    try:
        operation_ast = get_operation_ast(document)
        data: "EventDataType" = {"query": print_ast(document)}

        if operation_ast is not None:
            if has_data_collection_enabled(client_options):
                if client_options["data_collection"]["graphql"]["variables"]:
                    data["variables"] = operation_ast.variable_definitions
            elif should_send_default_pii():
                data["variables"] = operation_ast.variable_definitions

            if operation_ast.name is not None:
                data["operationName"] = operation_ast.name.value

        return data
    except (AttributeError, TypeError):
        return dict()


def _transport_method(transport: "Union[Transport, AsyncTransport]") -> str:
    """
    The RequestsHTTPTransport allows defining the HTTP method; all
    other transports use POST.
    """
    try:
        return transport.method
    except AttributeError:
        return "POST"


def _request_info_from_transport(
    transport: "Union[Transport, AsyncTransport, None]",
) -> "Dict[str, str]":
    if transport is None:
        return {}

    request_info = {
        "method": _transport_method(transport),
    }

    try:
        url = transport.url
    except AttributeError:
        return request_info

    with capture_internal_exceptions():
        client_options = sentry_sdk.get_client().options
        use_data_collection = has_data_collection_enabled(client_options)

        # Some transports accept a URL object as well as a string.
        # Credentials are always removed from the URL. Query values are
        # filtered here only when data_collection is not configured, since
        # its url_query_params setting does its own filtering.
        parsed_url = parse_url(str(url), sanitize=not use_data_collection)
        request_info["url"] = parsed_url.url

        query_string: "Optional[str]" = parsed_url.query
        if use_data_collection and query_string:
            query_string = _apply_data_collection_filtering_to_query_string(
                query_string=query_string,
                behaviour=client_options["data_collection"]["url_query_params"],
            )
        if query_string:
            request_info["query_string"] = query_string

    return request_info


def _patch_execute() -> None:
    real_execute = gql.Client.execute

    # Maintain signature for backwards compatibility.
    # gql.Client.execute() accepts a positional-only "request"
    # parameter with version 4.0.0.
    @ensure_integration_enabled(GQLIntegration, real_execute)
    def sentry_patched_execute(
        self: "gql.Client",
        document: "DocumentNode",
        *args: "Any",
        **kwargs: "Any",
    ) -> "Any":
        scope = sentry_sdk.get_isolation_scope()
        # document is a gql.GraphQLRequest with gql v4.0.0.
        scope.add_event_processor(_make_gql_event_processor(self, document))

        try:
            # document is a gql.GraphQLRequest with gql v4.0.0.
            return real_execute(self, document, *args, **kwargs)
        except TransportQueryError as e:
            event, hint = event_from_exception(
                e,
                client_options=sentry_sdk.get_client().options,
                mechanism={"type": "gql", "handled": False},
            )

            sentry_sdk.capture_event(event, hint)
            raise e

    gql.Client.execute = sentry_patched_execute


def _make_gql_event_processor(
    client: "gql.Client", document_or_request: "Union[DocumentNode, gql.GraphQLRequest]"
) -> "EventProcessor":
    def processor(event: "Event", hint: "dict[str, Any]") -> "Event":
        try:
            errors = hint["exc_info"][1].errors
        except (AttributeError, KeyError):
            errors = None

        request = event.setdefault("request", {})
        request.update(
            {
                "api_target": "graphql",
                **_request_info_from_transport(client.transport),
            }
        )

        client_options = sentry_sdk.get_client().options
        if has_data_collection_enabled(client_options):
            if client_options["data_collection"]["graphql"]["document"]:
                if GraphQLRequest is not None and isinstance(
                    document_or_request, GraphQLRequest
                ):
                    # In v4.0.0, gql moved to using GraphQLRequest instead of
                    # DocumentNode in execute
                    # https://github.com/graphql-python/gql/pull/556
                    document = document_or_request.document
                else:
                    document = document_or_request

                request["data"] = _data_from_document(document)
                contexts = event.setdefault("contexts", {})
                response = contexts.setdefault("response", {})
                response.update(
                    {
                        "data": {"errors": errors},
                        "type": response,
                    }
                )

        elif should_send_default_pii():
            if GraphQLRequest is not None and isinstance(
                document_or_request, GraphQLRequest
            ):
                # In v4.0.0, gql moved to using GraphQLRequest instead of
                # DocumentNode in execute
                # https://github.com/graphql-python/gql/pull/556
                document = document_or_request.document
            else:
                document = document_or_request

            request["data"] = _data_from_document(document)
            contexts = event.setdefault("contexts", {})
            response = contexts.setdefault("response", {})
            response.update(
                {
                    "data": {"errors": errors},
                    "type": response,
                }
            )

        return event

    return processor
