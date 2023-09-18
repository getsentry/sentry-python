from graphql.language.ast import OperationDefinitionNode

from sentry_sdk.consts import OP
from sentry_sdk.utils import capture_internal_exceptions
from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional, Tuple
    from graphql.language import DocumentNode, OperationDefinitionNode

OPERATION_TYPE_TO_OP = {
    "query": OP.HTTP_GRAPHQL_QUERY,
    "mutation": OP.HTTP_GRAPHQL_MUTATION,
    "subscription": OP.HTTP_GRAPHQL_SUBSCRIPTION,
}


def get_operation_type_and_name(document_node, operation_name=None):
    # type: (DocumentNode, Optional[str]) -> Tuple[Optional[str], Optional[str]]
    """
    Determine the operation type and name.

    If `operation_name` is given, search for an `OperationDefinitionNode`
    matching it; otherwise return the first `OperationDefinitionNode`'s type
    and name.
    """
    operation_type = None
    for definition in document_node.definitions:
        if isinstance(definition, OperationDefinitionNode):
            if operation_name is None:
                (
                    operation_type,
                    operation_name,
                ) = get_operation_type_and_name_from_operation_definition(definition)
            elif definition.name and definition.name.value == operation_name:
                (
                    operation_type,
                    _,
                ) = get_operation_type_and_name_from_operation_definition(definition)

    return operation_type, operation_name


def get_operation_type_and_name_from_operation_definition(operation_definition):
    # type: (OperationDefinitionNode) -> Optional[str]
    operation_type = None
    operation_name = None
    if operation_definition.name:
        operation_name = operation_definition.name.value
    operation_type = operation_definition.operation.value
    return operation_type, operation_name
