from contextlib import contextmanager
from collections.abc import Mapping, MutableMapping
from sentry_sdk.utils import event_from_exception
from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.integrations import DidNotEnable, Integration

try:
    import gql  # type: ignore[import]
    from graphql import print_ast, get_operation_ast, DocumentNode, VariableDefinitionNode  # type: ignore[import]
    from gql.transport import Transport, AsyncTransport  # type: ignore[import]
    from gql.transport.exceptions import TransportQueryError  # type: ignore[import]
except ImportError:
    raise DidNotEnable("gql is not installed")

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Dict, Generator, Tuple, Union
    from sentry_sdk._types import EventProcessor

    EventDataType = Dict[str, Union[str, Tuple[VariableDefinitionNode, ...]]]

MIN_GQL_VERSION = (3, 4, 1)


class GQLIntegration(Integration):
    identifier = "gql"

    @staticmethod
    def setup_once() -> None:
        if tuple(int(num) for num in gql.__version__.split(".")) < MIN_GQL_VERSION:
            raise DidNotEnable(
                "GQLIntegration is only supported for GQL versions %s and above."
                % ".".join(str(num) for num in MIN_GQL_VERSION)
            )
        _patch_execute()


def _data_from_document(document):
    # type: (DocumentNode) -> EventDataType
    try:
        operation_ast = get_operation_ast(document)
        data = {"query": print_ast(document)}  # type: EventDataType

        if operation_ast is not None:
            data["variables"] = operation_ast.variable_definitions
            if operation_ast.name is not None:
                data["operationName"] = operation_ast.name.value

        return data
    except (AttributeError, TypeError):
        return dict()


def _transport_method(transport):
    # type: (Union[Transport, AsyncTransport]) -> str
    """
    The RequestsHTTPTransport allows defining the HTTP method; all
    other transports use POST.
    """
    try:
        return transport.method
    except AttributeError:
        return "POST"


def _request_info_from_transport(transport):
    # type: (Union[Transport, AsyncTransport, None]) -> Dict[str, str]
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


def _patch_execute():
    # type: () -> None
    real_execute = gql.Client.execute

    def sentry_patched_execute(self, document, *args, **kwargs):
        # type: (gql.Client, DocumentNode, Any, Any) -> Any
        hub = Hub.current
        if hub.get_integration(GQLIntegration) is None:
            return real_execute(self, document, *args, **kwargs)

        with _graphql_event_processing(self, document):
            try:
                return real_execute(self, document, *args, **kwargs)
            except TransportQueryError as e:
                event, hint = event_from_exception(
                    e,
                    client_options=hub.client.options
                    if hub.client is not None
                    else None,
                    mechanism={"type": "gql", "handled": False},
                )

                hub.capture_event(event, hint)
                raise e

    gql.Client.execute = sentry_patched_execute


def _make_gql_event_processor(client, document):
    # type: (gql.Client, DocumentNode) -> EventProcessor
    def processor(event, hint):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        try:
            errors = hint["exc_info"][1].errors
        except (AttributeError, KeyError):
            errors = None
        _deep_update(
            event,
            {
                "request": {
                    "api_target": "graphql",
                    **_request_info_from_transport(client.transport),
                },
            },
        )

        if _should_send_default_pii():
            _deep_update(
                event,
                {
                    "request": {
                        "data": _data_from_document(document),
                    },
                    "contexts": {
                        "response": {
                            "data": {
                                "errors": errors,
                            },
                            "type": "response",
                        },
                    },
                },
            )

        return event

    return processor


@contextmanager
def _graphql_event_processing(client, document):
    # type: (gql.Client, DocumentNode) -> Generator[None, None, None]

    with Hub.current.configure_scope() as scope:
        scope.add_event_processor(_make_gql_event_processor(client, document))
        yield


def _deep_update(dictionary, update):
    # type: (MutableMapping[Any, Any], Mapping[Any, Any]) -> None
    for key, value in update.items():
        if isinstance(value, Mapping):
            if key not in dictionary or not isinstance(dictionary[key], MutableMapping):
                dictionary[key] = dict()
            _deep_update(dictionary[key], value)
        else:
            dictionary[key] = value
