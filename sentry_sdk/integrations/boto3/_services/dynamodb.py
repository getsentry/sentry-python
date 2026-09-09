import base64
import json
from typing import TYPE_CHECKING

from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3._services import _ServiceExtension
from sentry_sdk.integrations.boto3._services._attribute_extraction import (
    _as_boolean,
    _as_integer,
    _as_string,
    _as_string_list,
    _extract_attributes,
    _list_length,
)

if TYPE_CHECKING:
    from typing import Any, Dict, List, Optional, Sequence, Tuple

    from sentry_sdk.integrations.boto3._client import _ClientCallContext
    from sentry_sdk.integrations.boto3._services._attribute_extraction import (
        _AttributeSpec,
    )


_SPAN_ORIGIN = "auto.db.boto3"


def _json_default(value: "Any") -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    raise TypeError("value is not JSON serializable")


def _json_dumps(value: "Any") -> "Optional[str]":
    try:
        return json.dumps(
            value,
            # NaN is not valid JSON and must not become a span attribute.
            allow_nan=False,
            # binary DynamoDB attribute values are serialized as base64.
            default=_json_default,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        return None


def _json_list(value: "Any") -> "Optional[List[str]]":
    if not isinstance(value, list):
        return None

    serialized = []
    for item in value:
        # serialized separately since attributes such as
        # `aws.dynamodb.consumed_capacity` are defined as `string[]`.
        serialized_item = _json_dumps(item)
        if serialized_item is not None:
            serialized.append(serialized_item)
    # omit the attribute if every item failed.
    return serialized if serialized or not value else None


# flat request fields that map 1:1 onto span attributes.
# table names, provisioned throughput, and batch size are extracted separately.
# https://opentelemetry.io/docs/specs/semconv/db/dynamodb/
_REQUEST_ATTRIBUTES: "Dict[str, Sequence[_AttributeSpec]]" = {
    "CreateTable": (
        (
            "GlobalSecondaryIndexes",
            SPANDATA.AWS_DYNAMODB_GLOBAL_SECONDARY_INDEXES,
            _json_list,
        ),
        (
            "LocalSecondaryIndexes",
            SPANDATA.AWS_DYNAMODB_LOCAL_SECONDARY_INDEXES,
            _json_list,
        ),
    ),
    "GetItem": (
        (
            "ConsistentRead",
            SPANDATA.AWS_DYNAMODB_CONSISTENT_READ,
            _as_boolean,
        ),
        ("ProjectionExpression", SPANDATA.AWS_DYNAMODB_PROJECTION, _as_string),
    ),
    "ListTables": (
        (
            "ExclusiveStartTableName",
            SPANDATA.AWS_DYNAMODB_EXCLUSIVE_START_TABLE,
            _as_string,
        ),
        ("Limit", SPANDATA.AWS_DYNAMODB_LIMIT, _as_integer),
    ),
    "Query": (
        (
            "AttributesToGet",
            SPANDATA.AWS_DYNAMODB_ATTRIBUTES_TO_GET,
            _as_string_list,
        ),
        (
            "ConsistentRead",
            SPANDATA.AWS_DYNAMODB_CONSISTENT_READ,
            _as_boolean,
        ),
        ("IndexName", SPANDATA.AWS_DYNAMODB_INDEX_NAME, _as_string),
        ("Limit", SPANDATA.AWS_DYNAMODB_LIMIT, _as_integer),
        ("ProjectionExpression", SPANDATA.AWS_DYNAMODB_PROJECTION, _as_string),
        (
            "ScanIndexForward",
            SPANDATA.AWS_DYNAMODB_SCAN_FORWARD,
            _as_boolean,
        ),
        ("Select", SPANDATA.AWS_DYNAMODB_SELECT, _as_string),
    ),
    "Scan": (
        (
            "AttributesToGet",
            SPANDATA.AWS_DYNAMODB_ATTRIBUTES_TO_GET,
            _as_string_list,
        ),
        (
            "ConsistentRead",
            SPANDATA.AWS_DYNAMODB_CONSISTENT_READ,
            _as_boolean,
        ),
        ("IndexName", SPANDATA.AWS_DYNAMODB_INDEX_NAME, _as_string),
        ("Limit", SPANDATA.AWS_DYNAMODB_LIMIT, _as_integer),
        ("ProjectionExpression", SPANDATA.AWS_DYNAMODB_PROJECTION, _as_string),
        ("Segment", SPANDATA.AWS_DYNAMODB_SEGMENT, _as_integer),
        ("Select", SPANDATA.AWS_DYNAMODB_SELECT, _as_string),
        ("TotalSegments", SPANDATA.AWS_DYNAMODB_TOTAL_SEGMENTS, _as_integer),
    ),
    "UpdateTable": (
        (
            "AttributeDefinitions",
            SPANDATA.AWS_DYNAMODB_ATTRIBUTE_DEFINITIONS,
            _json_list,
        ),
        (
            "GlobalSecondaryIndexUpdates",
            SPANDATA.AWS_DYNAMODB_GLOBAL_SECONDARY_INDEX_UPDATES,
            _json_list,
        ),
    ),
}


# response fields that map 1:1 onto span attributes.
# `ConsumedCapacity` and `ItemCollectionMetrics` are extracted separately.
_RESPONSE_ATTRIBUTES: "Dict[str, Sequence[_AttributeSpec]]" = {
    "ListTables": (("TableNames", SPANDATA.AWS_DYNAMODB_TABLE_COUNT, _list_length),),
    "Scan": (
        ("Count", SPANDATA.AWS_DYNAMODB_COUNT, _as_integer),
        # how many items were scanned before filtering.
        ("ScannedCount", SPANDATA.AWS_DYNAMODB_SCANNED_COUNT, _as_integer),
    ),
}

# operations whose table is the `TableName` request parameter.
# `aws.dynamodb.table_names` is a single-element array.
_TABLE_NAME_OPERATIONS = frozenset(
    (
        "CreateTable",
        "DeleteItem",
        "DeleteTable",
        "DescribeTable",
        "GetItem",
        "PutItem",
        "Query",
        "Scan",
        "UpdateItem",
        "UpdateTable",
    )
)
# table names and batch size need to be extracted from the `RequestItems` parameter.
_REQUEST_ITEMS_OPERATIONS = frozenset(("BatchGetItem", "BatchWriteItem"))
# operations that may contain a `ProvisionedThroughput` parameter.
_THROUGHPUT_OPERATIONS = frozenset(("CreateTable", "UpdateTable"))
# operations that may return a `ConsumedCapacity` response field.
_CONSUMED_CAPACITY_OPERATIONS = frozenset(
    (
        "BatchGetItem",
        "BatchWriteItem",
        "CreateTable",
        "DeleteItem",
        "GetItem",
        "PutItem",
        "Query",
        "Scan",
        "UpdateItem",
        "UpdateTable",
    )
)
# operations that may return an `ItemCollectionMetrics` response field.
_ITEM_COLLECTION_METRICS_OPERATIONS = frozenset(
    ("BatchWriteItem", "CreateTable", "DeleteItem", "PutItem", "UpdateItem")
)


class _DynamoDbExtension(_ServiceExtension):
    __slots__ = ()

    def get_span_data(
        self, call_context: "_ClientCallContext"
    ) -> "Tuple[str, str, Dict[str, Any]]":
        attributes = {
            # `db.system.name` MUST be `aws.dynamodb` and SHOULD be set at span creation.
            # https://opentelemetry.io/docs/specs/semconv/db/dynamodb/
            SPANDATA.DB_SYSTEM_NAME: "aws.dynamodb",
            SPANDATA.DB_OPERATION_NAME: call_context.operation_name,
        }
        attributes.update(_get_request_attributes(call_context))
        return OP.DB, _SPAN_ORIGIN, attributes

    def get_response_span_attributes(
        self, call_context: "_ClientCallContext", response: "Any"
    ) -> "Dict[str, Any]":
        return _get_response_attributes(call_context.operation_name, response)


def _get_request_attributes(
    call_context: "_ClientCallContext",
) -> "Dict[str, Any]":
    operation_name = call_context.operation_name
    params = call_context.api_params
    attributes: "Dict[str, Any]" = {}

    # extract table name(s).
    table_names: "List[str]" = []
    if isinstance(params, dict):
        if operation_name in _TABLE_NAME_OPERATIONS:
            table_name = params.get("TableName")
            if isinstance(table_name, str) and table_name:
                table_names = [table_name]

        if operation_name in _REQUEST_ITEMS_OPERATIONS:
            request_items = params.get("RequestItems")
            if isinstance(request_items, dict):
                table_names = [
                    key for key in request_items if isinstance(key, str) and key
                ]

    if table_names:
        attributes[SPANDATA.AWS_DYNAMODB_TABLE_NAMES] = table_names
        if len(table_names) == 1:
            # `db.collection.name` is only valid when there is an unambiguous collection.
            # https://opentelemetry.io/docs/specs/semconv/db/database-spans/
            attributes[SPANDATA.DB_COLLECTION_NAME] = table_names[0]

    # extract attributes from the request parameters for this operation.
    attributes.update(
        _extract_attributes(params, _REQUEST_ATTRIBUTES.get(operation_name, ()))
    )

    # extract throughput attributes.
    if operation_name in _THROUGHPUT_OPERATIONS:
        attributes.update(_get_throughput_attributes(params))

    # extract batch size.
    if operation_name in _REQUEST_ITEMS_OPERATIONS and isinstance(params, dict):
        batch_size = _get_batch_size(operation_name, params)
        # omit if batch size is 1. one-item call to batch API is represented as a
        # single db operation. empty batches keep size 0.
        # https://opentelemetry.io/docs/specs/semconv/db/database-spans/
        if batch_size is not None:
            attributes[SPANDATA.DB_OPERATION_BATCH_SIZE] = batch_size

    return attributes


def _get_throughput_attributes(params: "Any") -> "Dict[str, Any]":
    if not isinstance(params, dict):
        return {}

    throughput = params.get("ProvisionedThroughput")
    if not isinstance(throughput, dict):
        return {}

    attributes = {}
    read_capacity = throughput.get("ReadCapacityUnits")
    if isinstance(read_capacity, (int, float)):
        attributes[SPANDATA.AWS_DYNAMODB_PROVISIONED_READ_CAPACITY] = float(
            read_capacity
        )

    write_capacity = throughput.get("WriteCapacityUnits")
    if isinstance(write_capacity, (int, float)):
        attributes[SPANDATA.AWS_DYNAMODB_PROVISIONED_WRITE_CAPACITY] = float(
            write_capacity
        )

    return attributes


def _get_batch_size(operation_name: str, params: "Any") -> "Optional[int]":
    request_items = params.get("RequestItems")
    if not isinstance(request_items, dict):
        return None

    batch_size = 0
    for table_request in request_items.values():
        if operation_name == "BatchGetItem" and isinstance(table_request, dict):
            items = table_request.get("Keys")
        elif operation_name == "BatchWriteItem":
            # write requests are the table's list value, not a nested `Keys` field.
            items = table_request
        else:
            items = None
        if isinstance(items, list):
            batch_size += len(items)

    return None if batch_size == 1 else batch_size


def _get_response_attributes(operation_name: str, response: "Any") -> "Dict[str, Any]":
    if not isinstance(response, dict):
        return {}

    # extract attributes from the response for this specific operation.
    attributes = _extract_attributes(
        response, _RESPONSE_ATTRIBUTES.get(operation_name, ())
    )

    if operation_name in _CONSUMED_CAPACITY_OPERATIONS:
        consumed_capacity = response.get("ConsumedCapacity")
        # single-table calls return one object; batch calls return a list.
        if isinstance(consumed_capacity, dict):
            consumed_capacity = [consumed_capacity]
        # spec wants `string[]` of each item.
        serialized_capacity = _json_list(consumed_capacity)
        if serialized_capacity is not None:
            attributes[SPANDATA.AWS_DYNAMODB_CONSUMED_CAPACITY] = serialized_capacity

    if operation_name in _ITEM_COLLECTION_METRICS_OPERATIONS:
        item_collection_metrics = response.get("ItemCollectionMetrics")
        if isinstance(item_collection_metrics, dict):
            # represented as a single JSON string.
            serialized_metrics = _json_dumps(item_collection_metrics)
            if serialized_metrics is not None:
                attributes[SPANDATA.AWS_DYNAMODB_ITEM_COLLECTION_METRICS] = (
                    serialized_metrics
                )

    return attributes
