from sentry_sdk.utils import event_from_exception
from sentry_sdk.hub import Hub
from sentry_sdk.integrations import DidNotEnable, Integration

try:
    import gql  # type: ignore[import]
    from graphql import print_ast, get_operation_ast, DocumentNode, VariableDefinitionNode  # type: ignore[import]
    from gql.transport import Transport, AsyncTransport  # type: ignore[import]
    from gql.transport.exceptions import TransportQueryError  # type: ignore[import]
except ImportError:
    raise DidNotEnable("gql is not installed")

from typing import Any, Dict, Optional, Tuple, Union

EventDataType = Dict[str, Union[str, Tuple[VariableDefinitionNode, ...]]]


class GQLIntegration(Integration):
    identifier = "gql"

    @staticmethod
    def setup_once() -> None:
        _patch_execute()


def _data_from_document(document: DocumentNode) -> EventDataType:
    operation_ast = get_operation_ast(document)
    data: EventDataType = {"query": print_ast(document)}

    if operation_ast is not None:
        data["variables"] = operation_ast.variable_definitions
        if operation_ast.name is not None:
            data["operationName"] = operation_ast.name.value

    return data


def _transport_method(transport: Union[Transport, AsyncTransport]) -> str:
    """
    The RequestsHTTPTransport allows defining the HTTP method; all
    other transports use POST.
    """
    try:
        return transport.method
    except AttributeError:
        return "POST"


def _request_info_from_transport(
    transport: Optional[Union[Transport, AsyncTransport]]
) -> Dict[str, str]:
    if transport is None:
        return {}

    request_info = {
        "method": _transport_method(transport),
    }

    try:
        request_info["url"] = transport.url
    except AttributeError:
        pass

    return request_info


def _patch_execute() -> None:
    real_execute = gql.Client.execute

    def sentry_patched_execute(
        self: gql.Client, document: DocumentNode, *args: Any, **kwargs: Any
    ) -> Any:
        try:
            return real_execute(self, document, *args, **kwargs)
        except TransportQueryError as e:
            hub = Hub.current
            event, hint = event_from_exception(
                e,
                client_options=hub.client.options if hub.client is not None else None,
                mechanism={"type": "gql", "handled": False},
            )
            event["request"] = {
                "data": _data_from_document(document),
                "api_target": "graphql",
                **_request_info_from_transport(self.transport),
            }

            event["contexts"] = {
                "response": {"data": {"errors": e.errors}, "type": "response"}
            }

            hub.capture_event(event, hint)
            raise e

    gql.Client.execute = sentry_patched_execute
