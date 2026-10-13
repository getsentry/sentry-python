import json
from copy import deepcopy

import boto3
import pytest
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3 import Boto3Integration
from sentry_sdk.integrations.boto3._context import AwsCallContext
from sentry_sdk.integrations.boto3._services.dynamodb import _DynamoDBExtension
from sentry_sdk.integrations.boto3.consts import AWS_RPC_SYSTEM_NAME, ORIGIN
from tests.integrations.boto3.helpers import (
    capture_spans_by_op,
    require_botocore_model_fields,
)
from tests.integrations.boto3.helpers import client_factory as client_factory

TABLE_ARN = "arn:aws:dynamodb:eu-north-1:123456789012:table/orders"


@pytest.fixture
def dynamodb_client(client_factory):
    return client_factory("dynamodb")


@pytest.fixture
def dynamodb_resource(client_factory):
    # `client_factory` initializes the sdk with the boto3 integration.
    session = boto3.Session(
        aws_access_key_id="-", aws_secret_access_key="-", region_name="eu-north-1"
    )
    resource = session.resource("dynamodb")
    yield resource
    # older supported botocore versions do not expose `BaseClient.close()`.
    close = getattr(resource.meta.client, "close", None)
    if close is not None:
        close()


def _stubbed_resource_span(resource, capture_items, method, call, response):
    """Return the span of `call`, which uses the resource api and makes one `method` call."""
    with Stubber(resource.meta.client) as stubber:
        stubber.add_response(method, response)
        spans = capture_spans_by_op(call, capture_items)
        stubber.assert_no_pending_responses()

    (span,) = spans[OP.DB]
    assert span["attributes"][SPANDATA.DB_SYSTEM_NAME] == "aws.dynamodb"
    return span


def _stubbed_span(client, capture_items, method, params, response):
    original = deepcopy(params)
    with Stubber(client) as stubber:
        stubber.add_response(method, response, expected_params=params)
        spans = capture_spans_by_op(
            lambda: getattr(client, method)(**params), capture_items
        )
        stubber.assert_no_pending_responses()
    assert params == original

    (span,) = spans[OP.DB]
    operation = client.meta.method_to_api_mapping[method]
    assert span.get("end_timestamp") is not None
    attributes = span.get("attributes", {})
    assert attributes[SPANDATA.SENTRY_OP] == OP.DB
    assert attributes[SPANDATA.SENTRY_ORIGIN] == ORIGIN
    assert attributes[SPANDATA.SENTRY_KIND] == "client"
    assert attributes[SPANDATA.CLOUD_PROVIDER] == "aws"
    assert attributes[SPANDATA.RPC_SYSTEM_NAME] == AWS_RPC_SYSTEM_NAME
    assert attributes[SPANDATA.RPC_SERVICE] == "DynamoDB"
    assert attributes[SPANDATA.RPC_METHOD] == operation
    assert attributes[SPANDATA.DB_SYSTEM_NAME] == "aws.dynamodb"
    assert attributes[SPANDATA.DB_OPERATION_NAME] == operation
    return span


def _query_parameters(attributes):
    prefix = SPANDATA.DB_QUERY_PARAMETER + "."
    return {
        key[len(prefix) :]: value
        for key, value in attributes.items()
        if key.startswith(prefix)
    }


@pytest.mark.parametrize(
    "method,params,response,expected_resource_id",
    [
        pytest.param(
            "list_tags_of_resource",
            {"ResourceArn": TABLE_ARN},
            {"Tags": []},
            TABLE_ARN,
            id="arn",
        ),
        pytest.param(
            "delete_table",
            {"TableName": "orders"},
            {},
            None,
            id="name",
        ),
    ],
)
def test_resource_attributes(
    dynamodb_client, capture_items, method, params, response, expected_resource_id
):
    span = _stubbed_span(dynamodb_client, capture_items, method, params, response)

    attributes = span["attributes"]
    if expected_resource_id is None:
        assert SPANDATA.CLOUD_ACCOUNT_ID not in attributes
        assert SPANDATA.CLOUD_RESOURCE_ID not in attributes
    else:
        assert attributes[SPANDATA.CLOUD_ACCOUNT_ID] == "123456789012"
        assert attributes[SPANDATA.CLOUD_RESOURCE_ID] == expected_resource_id


@pytest.mark.parametrize(
    "table_name", [pytest.param("orders", id="name"), pytest.param(TABLE_ARN, id="arn")]
)
def test_get_item(dynamodb_client, capture_items, table_name):
    params = {
        "TableName": table_name,
        "Key": {"id": {"S": "private-key"}},
        "ConsistentRead": True,
        "ProjectionExpression": "id, total",
        "ReturnConsumedCapacity": "TOTAL",
    }
    response = {
        "Item": {"id": {"S": "private-key"}, "total": {"N": "42"}},
        "ConsumedCapacity": {"TableName": "orders", "CapacityUnits": 0.5},
    }

    span = _stubbed_span(dynamodb_client, capture_items, "get_item", params, response)

    assert span["name"] == "GetItem orders"
    attributes = span["attributes"]
    assert attributes[SPANDATA.DB_COLLECTION_NAME] == "orders"
    assert attributes[SPANDATA.AWS_DYNAMODB_TABLE_NAMES] == ["orders"]
    assert attributes[SPANDATA.AWS_DYNAMODB_CONSISTENT_READ] is True
    assert attributes[SPANDATA.AWS_DYNAMODB_PROJECTION] == "id, total"
    assert attributes[SPANDATA.AWS_DYNAMODB_CONSUMED_CAPACITY] == [
        '{"TableName":"orders","CapacityUnits":0.5}'
    ]
    assert SPANDATA.DB_OPERATION_BATCH_SIZE not in attributes
    assert SPANDATA.DB_QUERY_TEXT not in attributes
    # a call without query text is summarized and normalized to its operation and table.
    assert attributes[SPANDATA.DB_QUERY_SUMMARY] == "GetItem orders"
    assert attributes[SPANDATA.SENTRY_NORMALIZED_DB_QUERY] == "GetItem orders"
    assert "private-key" not in json.dumps(span)


def test_query(dynamodb_client, capture_items):
    params = {
        "TableName": "orders",
        "IndexName": "by-customer",
        "KeyConditionExpression": "customer = :customer",
        "ExpressionAttributeValues": {":customer": {"S": "private-customer"}},
        "AttributesToGet": ["id", "total"],
        "Limit": 10,
        "ScanIndexForward": False,
        "Select": "SPECIFIC_ATTRIBUTES",
    }
    response = {"Items": [], "Count": 0, "ScannedCount": 3}

    span = _stubbed_span(dynamodb_client, capture_items, "query", params, response)

    assert span["name"] == "Query orders"
    attributes = span["attributes"]
    assert attributes[SPANDATA.AWS_DYNAMODB_INDEX_NAME] == "by-customer"
    assert attributes[SPANDATA.AWS_DYNAMODB_ATTRIBUTES_TO_GET] == ["id", "total"]
    assert attributes[SPANDATA.AWS_DYNAMODB_LIMIT] == 10
    assert attributes[SPANDATA.AWS_DYNAMODB_SCAN_FORWARD] is False
    assert attributes[SPANDATA.AWS_DYNAMODB_SELECT] == "SPECIFIC_ATTRIBUTES"
    assert attributes[SPANDATA.AWS_DYNAMODB_COUNT] == 0
    assert attributes[SPANDATA.AWS_DYNAMODB_SCANNED_COUNT] == 3
    assert "private-customer" not in json.dumps(span)


def test_scan(dynamodb_client, capture_items):
    params = {"TableName": "orders", "Segment": 1, "TotalSegments": 4}

    span = _stubbed_span(
        dynamodb_client, capture_items, "scan", params, {"Count": 2, "ScannedCount": 2}
    )

    assert span["name"] == "Scan orders"
    attributes = span["attributes"]
    assert attributes[SPANDATA.AWS_DYNAMODB_SEGMENT] == 1
    assert attributes[SPANDATA.AWS_DYNAMODB_TOTAL_SEGMENTS] == 4


def test_list_tables(dynamodb_client, capture_items):
    params = {"ExclusiveStartTableName": "customers", "Limit": 2}

    span = _stubbed_span(
        dynamodb_client,
        capture_items,
        "list_tables",
        params,
        {"TableNames": ["invoices", "orders"]},
    )

    # without the endpoint, which botocore may resolve to one with the account id.
    assert span["name"] == "ListTables"
    attributes = span["attributes"]
    assert attributes[SPANDATA.AWS_DYNAMODB_EXCLUSIVE_START_TABLE] == "customers"
    assert attributes[SPANDATA.AWS_DYNAMODB_LIMIT] == 2
    assert attributes[SPANDATA.AWS_DYNAMODB_TABLE_COUNT] == 2
    assert SPANDATA.DB_COLLECTION_NAME not in attributes
    assert SPANDATA.AWS_DYNAMODB_TABLE_NAMES not in attributes
    assert attributes[SPANDATA.DB_QUERY_SUMMARY] == "ListTables"
    assert attributes[SPANDATA.SENTRY_NORMALIZED_DB_QUERY] == "ListTables"


def test_create_table(dynamodb_client, capture_items):
    key_schema = [{"AttributeName": "id", "KeyType": "HASH"}]
    params = {
        "TableName": "orders",
        "AttributeDefinitions": [
            {"AttributeName": "id", "AttributeType": "S"},
            {"AttributeName": "customer", "AttributeType": "S"},
        ],
        "KeySchema": key_schema,
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "by-customer",
                "KeySchema": [{"AttributeName": "customer", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": {
                    "ReadCapacityUnits": 1,
                    "WriteCapacityUnits": 1,
                },
            }
        ],
        "LocalSecondaryIndexes": [
            {
                "IndexName": "by-id",
                "KeySchema": key_schema,
                "Projection": {"ProjectionType": "KEYS_ONLY"},
            }
        ],
        "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 2},
    }

    span = _stubbed_span(dynamodb_client, capture_items, "create_table", params, {})

    assert span["name"] == "CreateTable orders"
    attributes = span["attributes"]
    assert attributes[SPANDATA.AWS_DYNAMODB_ATTRIBUTE_DEFINITIONS] == [
        '{"AttributeName":"id","AttributeType":"S"}',
        '{"AttributeName":"customer","AttributeType":"S"}',
    ]
    assert attributes[SPANDATA.AWS_DYNAMODB_GLOBAL_SECONDARY_INDEXES] == [
        '{"IndexName":"by-customer","KeySchema":[{"AttributeName":"customer","KeyType":"HASH"}],'
        '"Projection":{"ProjectionType":"ALL"},'
        '"ProvisionedThroughput":{"ReadCapacityUnits":1,"WriteCapacityUnits":1}}'
    ]
    assert attributes[SPANDATA.AWS_DYNAMODB_LOCAL_SECONDARY_INDEXES] == [
        '{"IndexName":"by-id","KeySchema":[{"AttributeName":"id","KeyType":"HASH"}],'
        '"Projection":{"ProjectionType":"KEYS_ONLY"}}'
    ]
    assert attributes[SPANDATA.AWS_DYNAMODB_PROVISIONED_READ_CAPACITY] == 5.0
    assert attributes[SPANDATA.AWS_DYNAMODB_PROVISIONED_WRITE_CAPACITY] == 2.0


def test_update_table(dynamodb_client, capture_items):
    params = {
        "TableName": "orders",
        "GlobalSecondaryIndexUpdates": [{"Delete": {"IndexName": "by-customer"}}],
    }

    span = _stubbed_span(dynamodb_client, capture_items, "update_table", params, {})

    attributes = span["attributes"]
    assert attributes[SPANDATA.AWS_DYNAMODB_GLOBAL_SECONDARY_INDEX_UPDATES] == [
        '{"Delete":{"IndexName":"by-customer"}}'
    ]
    assert SPANDATA.AWS_DYNAMODB_PROVISIONED_READ_CAPACITY not in attributes


@pytest.mark.parametrize(
    "request_items,expected_name,expected_collection,expected_batch_size",
    [
        pytest.param(
            {
                "orders": {"Keys": [{"id": {"S": "1"}}, {"id": {"S": "2"}}]},
                TABLE_ARN.replace("orders", "invoices"): {"Keys": [{"id": {"S": "3"}}]},
            },
            "BatchGetItem invoices orders",
            ",invoices,orders,",
            3,
            id="two-tables",
        ),
        pytest.param(
            {"orders": {"Keys": [{"id": {"S": "1"}}, {"id": {"S": "2"}}]}},
            "BatchGetItem orders",
            "orders",
            2,
            id="one-table",
        ),
        pytest.param(
            {"orders": {"Keys": [{"id": {"S": "1"}}]}},
            "BatchGetItem orders",
            "orders",
            None,
            id="one-key",
        ),
    ],
)
def test_batch_get_item(
    dynamodb_client,
    capture_items,
    request_items,
    expected_name,
    expected_collection,
    expected_batch_size,
):
    params = {"RequestItems": request_items, "ReturnConsumedCapacity": "TOTAL"}
    response = {
        "Responses": {},
        "UnprocessedKeys": {},
        "ConsumedCapacity": [
            {"TableName": "orders", "CapacityUnits": 1.0},
            {"TableName": "invoices", "CapacityUnits": 0.5},
        ],
    }

    span = _stubbed_span(
        dynamodb_client, capture_items, "batch_get_item", params, response
    )

    assert span["name"] == expected_name
    assert span["status"] == "ok"
    attributes = span["attributes"]
    # sorted, independent of the request order.
    assert attributes[SPANDATA.AWS_DYNAMODB_TABLE_NAMES] == sorted(
        ["orders", "invoices"][: len(request_items)]
    )
    assert attributes.get(SPANDATA.DB_COLLECTION_NAME) == expected_collection
    assert attributes.get(SPANDATA.DB_OPERATION_BATCH_SIZE) == expected_batch_size
    # the operation and its sorted tables, like the name.
    assert attributes[SPANDATA.DB_QUERY_SUMMARY] == expected_name
    assert attributes[SPANDATA.SENTRY_NORMALIZED_DB_QUERY] == expected_name
    assert attributes[SPANDATA.AWS_DYNAMODB_CONSUMED_CAPACITY] == [
        '{"TableName":"orders","CapacityUnits":1.0}',
        '{"TableName":"invoices","CapacityUnits":0.5}',
    ]


def test_batch_write_item(dynamodb_client, capture_items):
    params = {
        "RequestItems": {
            "orders": [
                {"PutRequest": {"Item": {"id": {"S": "1"}}}},
                {"DeleteRequest": {"Key": {"id": {"S": "2"}}}},
            ]
        }
    }

    span = _stubbed_span(dynamodb_client, capture_items, "batch_write_item", params, {})

    assert span["name"] == "BatchWriteItem orders"
    assert span["attributes"][SPANDATA.DB_OPERATION_BATCH_SIZE] == 2


def test_batch_write_item_multiple_tables(dynamodb_client, capture_items):
    params = {
        "RequestItems": {
            "audit-log": [{"PutRequest": {"Item": {"id": {"S": "1"}}}}],
            "Orders": [{"PutRequest": {"Item": {"id": {"S": "2"}}}}],
        }
    }

    span = _stubbed_span(dynamodb_client, capture_items, "batch_write_item", params, {})

    # sorted and case-preserving, independent of the request order.
    assert span["name"] == "BatchWriteItem Orders audit-log"
    attributes = span["attributes"]
    assert attributes[SPANDATA.DB_COLLECTION_NAME] == ",Orders,audit-log,"
    assert attributes[SPANDATA.AWS_DYNAMODB_TABLE_NAMES] == ["Orders", "audit-log"]


def test_transact_write_items(dynamodb_client, capture_items):
    params = {
        "TransactItems": [
            {"Put": {"TableName": "orders", "Item": {"id": {"S": "1"}}}},
            {
                "ConditionCheck": {
                    "TableName": "customers",
                    "Key": {"id": {"S": "2"}},
                    "ConditionExpression": "attribute_exists(id)",
                }
            },
            {"Delete": {"TableName": "orders", "Key": {"id": {"S": "3"}}}},
        ],
        "ReturnItemCollectionMetrics": "SIZE",
    }
    response = {
        "ItemCollectionMetrics": {
            "orders": [
                {
                    "ItemCollectionKey": {"id": {"S": "1"}},
                    "SizeEstimateRangeGB": [0.0, 1.0],
                }
            ]
        }
    }

    span = _stubbed_span(
        dynamodb_client, capture_items, "transact_write_items", params, response
    )

    assert span["name"] == "TransactWriteItems customers orders"
    attributes = span["attributes"]
    assert attributes[SPANDATA.AWS_DYNAMODB_TABLE_NAMES] == ["customers", "orders"]
    assert attributes[SPANDATA.DB_COLLECTION_NAME] == ",customers,orders,"
    assert attributes[SPANDATA.DB_OPERATION_BATCH_SIZE] == 3
    assert attributes[SPANDATA.AWS_DYNAMODB_ITEM_COLLECTION_METRICS] == (
        '{"orders":[{"ItemCollectionKey":{"id":{"S":"1"}},"SizeEstimateRangeGB":[0.0,1.0]}]}'
    )


def test_transact_get_items(dynamodb_client, capture_items):
    params = {
        "TransactItems": [
            {"Get": {"TableName": "orders", "Key": {"id": {"S": "1"}}}},
            {"Get": {"TableName": "orders", "Key": {"id": {"S": "2"}}}},
        ]
    }

    span = _stubbed_span(
        dynamodb_client, capture_items, "transact_get_items", params, {}
    )

    assert span["name"] == "TransactGetItems orders"
    attributes = span["attributes"]
    assert attributes[SPANDATA.AWS_DYNAMODB_TABLE_NAMES] == ["orders"]
    assert attributes[SPANDATA.DB_OPERATION_BATCH_SIZE] == 2


def test_item_collection_metrics_binary_key(dynamodb_client, capture_items):
    params = {
        "TableName": "orders",
        "Item": {"id": {"B": b"\x00"}},
        "ReturnItemCollectionMetrics": "SIZE",
    }
    response = {
        "ItemCollectionMetrics": {
            "ItemCollectionKey": {"id": {"B": b"\x00"}},
            "SizeEstimateRangeGB": [0.0, 1.0],
        }
    }

    span = _stubbed_span(dynamodb_client, capture_items, "put_item", params, response)

    assert span["name"] == "PutItem orders"
    assert SPANDATA.AWS_DYNAMODB_ITEM_COLLECTION_METRICS not in span["attributes"]


@pytest.mark.parametrize(
    "key,expected_key",
    [
        pytest.param({"id": {"S": "1"}}, '{"id":{"S":"1"}}', id="string"),
        pytest.param({"id": {"N": "10"}}, '{"id":{"N":"10"}}', id="number"),
        pytest.param({"id": {"B": b"\x00"}}, None, id="binary"),
    ],
)
def test_item_collection_metrics_resource(
    dynamodb_resource, capture_items, key, expected_key
):
    table = dynamodb_resource.Table("orders")
    response = {
        "ItemCollectionMetrics": {
            "ItemCollectionKey": key,
            "SizeEstimateRangeGB": [0.0, 1.0],
        }
    }

    span = _stubbed_resource_span(
        dynamodb_resource,
        capture_items,
        "put_item",
        lambda: table.put_item(Item={"id": "1"}, ReturnItemCollectionMetrics="SIZE"),
        response,
    )

    # the resource api parses the key to python values; it's sent typed, like the
    # low-level client's.
    attributes = span["attributes"]
    if expected_key is None:
        assert SPANDATA.AWS_DYNAMODB_ITEM_COLLECTION_METRICS not in attributes
    else:
        assert attributes[SPANDATA.AWS_DYNAMODB_ITEM_COLLECTION_METRICS] == (
            '{"ItemCollectionKey":%s,"SizeEstimateRangeGB":[0.0,1.0]}' % expected_key
        )


def test_item_collection_metrics_resource_batch(dynamodb_resource, capture_items):
    response = {
        "UnprocessedItems": {},
        "ItemCollectionMetrics": {
            "orders": [
                {
                    "ItemCollectionKey": {"customer": {"N": "7"}},
                    "SizeEstimateRangeGB": [0.0, 1.0],
                }
            ]
        },
    }

    span = _stubbed_resource_span(
        dynamodb_resource,
        capture_items,
        "batch_write_item",
        lambda: dynamodb_resource.batch_write_item(
            RequestItems={"orders": [{"PutRequest": {"Item": {"customer": 7}}}]},
            ReturnItemCollectionMetrics="SIZE",
        ),
        response,
    )

    assert span["attributes"][SPANDATA.AWS_DYNAMODB_ITEM_COLLECTION_METRICS] == (
        '{"orders":[{"ItemCollectionKey":{"customer":{"N":"7"}},"SizeEstimateRangeGB":[0.0,1.0]}]}'
    )


def test_execute_statement_resource_client(dynamodb_resource, capture_items):
    client = dynamodb_resource.meta.client
    require_botocore_model_fields(client, "execute_statement")
    statement = 'SELECT * FROM "orders"."by-customer" WHERE customer = ? AND total > ?'
    # the resource api's client takes python values instead of typed values.
    parameters = ["alex", 42, True, None, {"city": "Ghent"}, b"\x00", {"gift"}]

    span = _stubbed_resource_span(
        dynamodb_resource,
        capture_items,
        "execute_statement",
        lambda: client.execute_statement(Statement=statement, Parameters=parameters),
        {"Items": []},
    )

    assert span["name"] == "SELECT orders"
    attributes = span["attributes"]
    assert attributes[SPANDATA.DB_QUERY_TEXT] == statement
    assert attributes[SPANDATA.DB_COLLECTION_NAME] == "orders"
    assert attributes[SPANDATA.AWS_DYNAMODB_INDEX_NAME] == "by-customer"
    # values as the typed parameters the call sends; the binary one is omitted.
    assert _query_parameters(attributes) == {
        "0": "alex",
        "1": "42",
        "2": "true",
        "3": "null",
        "4": '{"M":{"city":{"S":"Ghent"}}}',
        "6": '["gift"]',
    }


@pytest.mark.parametrize(
    "method,params,response,expected",
    [
        pytest.param(
            "execute_statement",
            {
                "Statement": 'SELECT * FROM "orders"."by-status" WHERE s = ? AND t = \'inline\'',
                "Parameters": [{"S": "paid"}],
            },
            {"Items": []},
            'SELECT * FROM "orders"."by-status" WHERE s = %s AND t = %s',
            id="parameterized-with-literal",
        ),
        pytest.param(
            "execute_statement",
            {
                "Statement": "-- report\nDELETE FROM orders WHERE id = 'o-2025' /* old */"
            },
            {"Items": []},
            "DELETE FROM orders WHERE id = %s",
            id="literals-and-comments",
        ),
        pytest.param(
            "execute_statement",
            {
                "Statement": "SELECT *\n  FROM orders\n  WHERE id = ?",
                "Parameters": [{"S": "1"}],
            },
            {"Items": []},
            "SELECT * FROM orders WHERE id = %s",
            id="whitespace",
        ),
        pytest.param(
            "batch_execute_statement",
            {
                "Statements": [
                    {"Statement": "UPDATE orders SET s = 'paid' WHERE id = '1'"},
                    {"Statement": "UPDATE orders SET s = 'sent' WHERE id = '2'"},
                ]
            },
            {"Responses": []},
            "BATCH UPDATE orders SET s = %s WHERE id = %s",
            id="batch-shared",
        ),
        pytest.param(
            "batch_execute_statement",
            {
                "Statements": [
                    {"Statement": "SELECT * FROM orders WHERE id = '1'"},
                    {"Statement": "SELECT * FROM customers WHERE id = '2'"},
                ]
            },
            {"Responses": []},
            "SELECT * FROM orders WHERE id = %s; SELECT * FROM customers WHERE id = %s",
            id="batch-different",
        ),
        pytest.param(
            "execute_statement",
            {"Statement": "SELECT * FROM orders WHERE id = 'unterminated"},
            {"Items": []},
            "SELECT orders",
            id="unsanitizable",
        ),
    ],
)
def test_partiql_normalized_db_query(
    dynamodb_client, capture_items, method, params, response, expected
):
    require_botocore_model_fields(dynamodb_client, method)

    span = _stubbed_span(dynamodb_client, capture_items, method, params, response)

    # set by the sdk, so relay doesn't normalize partiql as sql; calls without query
    # text are covered by `test_get_item`, `test_batch_get_item` and `test_list_tables`.
    assert span["attributes"][SPANDATA.SENTRY_NORMALIZED_DB_QUERY] == expected


def test_query_resource(dynamodb_resource, capture_items):
    table = dynamodb_resource.Table("orders")

    span = _stubbed_resource_span(
        dynamodb_resource,
        capture_items,
        "query",
        # the resource api builds condition objects into expressions after the span starts.
        lambda: table.query(
            IndexName="by-customer",
            KeyConditionExpression=Key("customer").eq("private-customer")
            & Key("created").gte(5),
            FilterExpression=Attr("total").gt(10),
        ),
        {"Items": [], "Count": 0, "ScannedCount": 0},
    )

    assert span["name"] == "Query orders"
    attributes = span["attributes"]
    assert attributes[SPANDATA.DB_COLLECTION_NAME] == "orders"
    assert attributes[SPANDATA.AWS_DYNAMODB_INDEX_NAME] == "by-customer"
    assert attributes[SPANDATA.SENTRY_NORMALIZED_DB_QUERY] == "Query orders"
    assert SPANDATA.DB_QUERY_TEXT not in attributes
    assert "private-customer" not in json.dumps(span)


def test_span_name_without_endpoint():
    # botocore always resolves an endpoint; without one, the generic name is kept.
    ctx = AwsCallContext("ListTables", {})
    assert _DynamoDBExtension().get_span_name(ctx, {}) is None


def test_client_error(dynamodb_client, capture_items):
    params = {"TableName": "orders", "Key": {"id": {"S": "1"}}}

    with Stubber(dynamodb_client) as stubber:
        stubber.add_client_error(
            "get_item",
            service_error_code="ResourceNotFoundException",
            http_status_code=400,
            expected_params=params,
        )

        def call():
            with pytest.raises(ClientError):
                dynamodb_client.get_item(**params)

        spans = capture_spans_by_op(call, capture_items)

    (span,) = spans[OP.DB]
    assert span["name"] == "GetItem orders"
    assert span["status"] == "error"
    assert span["attributes"][SPANDATA.ERROR_TYPE] == "ResourceNotFoundException"
    assert span["attributes"][SPANDATA.DB_COLLECTION_NAME] == "orders"


@pytest.mark.parametrize(
    "parameters,expected_parameters",
    [
        pytest.param(
            [
                {"S": "alex"},
                {"N": "42"},
                {"BOOL": False},
                {"NULL": True},
                {"SS": ["a", "b"]},
                {"NS": ["1", "2"]},
                {"L": [{"S": "a"}, {"N": "1"}]},
                {"M": {"id": {"S": "1"}}},
            ],
            {
                "0": "alex",
                "1": "42",
                "2": "false",
                "3": "null",
                "4": '["a","b"]',
                "5": '["1","2"]',
                "6": '{"L":[{"S":"a"},{"N":"1"}]}',
                "7": '{"M":{"id":{"S":"1"}}}',
            },
            id="types",
        ),
        pytest.param(
            [
                {"B": b"\x00"},
                {"BS": [b"\x00"]},
                {"L": [{"S": "a"}, {"B": b"\x00"}]},
                {"M": {"nested": {"M": {"id": {"B": b"\x00"}}}}},
                {"S": "kept"},
            ],
            {"4": "kept"},
            id="binary",
        ),
    ],
)
def test_execute_statement_parameters(
    dynamodb_client, capture_items, parameters, expected_parameters
):
    require_botocore_model_fields(dynamodb_client, "execute_statement")
    statement = 'SELECT * FROM "orders" WHERE customer = ? AND status = ?'
    params = {"Statement": statement, "Parameters": parameters}

    span = _stubbed_span(
        dynamodb_client, capture_items, "execute_statement", params, {"Items": []}
    )

    assert span["name"] == "SELECT orders"
    attributes = span["attributes"]
    assert attributes[SPANDATA.DB_QUERY_SUMMARY] == "SELECT orders"
    # parameterized statements are used as provided.
    assert attributes[SPANDATA.DB_QUERY_TEXT] == statement
    assert attributes[SPANDATA.DB_COLLECTION_NAME] == "orders"
    assert attributes[SPANDATA.AWS_DYNAMODB_TABLE_NAMES] == ["orders"]
    assert _query_parameters(attributes) == expected_parameters
    assert SPANDATA.DB_OPERATION_BATCH_SIZE not in attributes


@pytest.mark.parametrize(
    "method,params,expected_index",
    [
        pytest.param(
            "execute_statement",
            {"Statement": 'SELECT * FROM "orders"."by-customer" WHERE customer = ?'},
            "by-customer",
            id="quoted",
        ),
        pytest.param(
            "execute_statement",
            {"Statement": "select * from orders . by_status where status = ?"},
            "by_status",
            id="bare",
        ),
        pytest.param(
            "execute_statement",
            {"Statement": 'SELECT * FROM "orders" WHERE "orders".id = ?'},
            None,
            id="table",
        ),
        pytest.param(
            "batch_execute_statement",
            {
                "Statements": [
                    {"Statement": 'SELECT * FROM "orders"."by-status" WHERE s = ?'},
                    {"Statement": 'SELECT * FROM "orders"."by-status" WHERE s = ?'},
                ]
            },
            "by-status",
            id="batch-same-index",
        ),
        pytest.param(
            "batch_execute_statement",
            {
                "Statements": [
                    {"Statement": 'SELECT * FROM "orders"."by-status" WHERE s = ?'},
                    {"Statement": 'SELECT * FROM "orders" WHERE id = ?'},
                ]
            },
            None,
            id="batch-different-index",
        ),
    ],
)
def test_partiql_index(dynamodb_client, capture_items, method, params, expected_index):
    require_botocore_model_fields(dynamodb_client, method)
    response = {"Items": []} if method == "execute_statement" else {"Responses": []}

    span = _stubbed_span(dynamodb_client, capture_items, method, params, response)

    attributes = span["attributes"]
    assert attributes.get(SPANDATA.AWS_DYNAMODB_INDEX_NAME) == expected_index
    assert attributes[SPANDATA.DB_COLLECTION_NAME] == "orders"


def test_execute_statement_parameters_with_comment(dynamodb_client, capture_items):
    require_botocore_model_fields(dynamodb_client, "execute_statement")
    statement = '/* order lookup */ SELECT * FROM "orders" WHERE id = ? -- don\'t cache'
    params = {"Statement": statement, "Parameters": [{"S": "1"}]}

    span = _stubbed_span(
        dynamodb_client, capture_items, "execute_statement", params, {"Items": []}
    )

    assert span["name"] == "SELECT orders"
    attributes = span["attributes"]
    # parameterized statements are used as provided, comments included.
    assert attributes[SPANDATA.DB_QUERY_TEXT] == statement
    assert attributes[SPANDATA.DB_COLLECTION_NAME] == "orders"


@pytest.mark.parametrize(
    "statement,expected_text,expected_summary,expected_table",
    [
        pytest.param(
            "SELECT id FROM \"orders\".\"by-customer\" WHERE customer = 'it''s' AND total > 12.5e3",
            'SELECT id FROM "orders"."by-customer" WHERE customer = ? AND total > ?',
            "SELECT orders",
            "orders",
            id="select-index",
        ),
        pytest.param(
            "insert into orders value {'id': '1', 'tags': <<'a', 'b'>>, 'items': [1, [2]]}",
            "insert into orders value ?",
            "INSERT orders",
            "orders",
            id="insert-tuple-bag-list",
        ),
        pytest.param(
            'UPDATE "my-orders" SET tags[0] = `{a: 1}`, open = true '
            "WHERE id IN ['1', '2'] AND closed IS NOT MISSING AND note IS NULL AND v = null",
            'UPDATE "my-orders" SET tags[?] = ?, open = ? '
            "WHERE id IN ? AND closed IS NOT MISSING AND note IS NULL AND v = ?",
            "UPDATE my-orders",
            "my-orders",
            id="update-predicates",
        ),
        pytest.param(
            "DELETE FROM orders WHERE id = '1'",
            "DELETE FROM orders WHERE id = ?",
            "DELETE orders",
            "orders",
            id="delete",
        ),
        pytest.param(
            "SELECT * FROM orders WHERE id = 'unterminated",
            None,
            "SELECT orders",
            "orders",
            id="unterminated-string",
        ),
        pytest.param(
            "SELECT * FROM orders WHERE id IN [1, 2",
            None,
            "SELECT orders",
            "orders",
            id="unterminated-list",
        ),
        pytest.param(
            "SELECT * FROM orders WHERE id IN ['a', 'b]",
            None,
            "SELECT orders",
            "orders",
            id="unterminated-string-in-list",
        ),
        pytest.param(
            "REMOVE x",
            "REMOVE x",
            "ExecuteStatement",
            None,
            id="unrecognized",
        ),
        pytest.param(
            "-- monthly report\nSELECT * FROM \"orders\" WHERE id = 'a' "
            "/* customer's lookup */ AND total > 5 -- don't log",
            'SELECT * FROM "orders" WHERE id = ? AND total > ?',
            "SELECT orders",
            "orders",
            id="comments",
        ),
        pytest.param(
            "SELECT * FROM \"orders--archive\" WHERE note = '-- /* not a comment'",
            'SELECT * FROM "orders--archive" WHERE note = ?',
            "SELECT orders--archive",
            "orders--archive",
            id="comment-markers-in-tokens",
        ),
        pytest.param(
            "SELECT * FROM orders WHERE id = 'a' /* unterminated",
            "SELECT * FROM orders WHERE id = ?",
            "SELECT orders",
            "orders",
            id="unterminated-comment",
        ),
    ],
)
def test_execute_statement_without_parameters(
    dynamodb_client,
    capture_items,
    statement,
    expected_text,
    expected_summary,
    expected_table,
):
    require_botocore_model_fields(dynamodb_client, "execute_statement")
    params = {"Statement": statement}

    span = _stubbed_span(
        dynamodb_client, capture_items, "execute_statement", params, {"Items": []}
    )

    attributes = span["attributes"]
    assert attributes.get(SPANDATA.DB_QUERY_TEXT) == expected_text
    assert attributes.get(SPANDATA.DB_QUERY_SUMMARY) == expected_summary
    assert attributes.get(SPANDATA.DB_COLLECTION_NAME) == expected_table
    assert span["name"] == expected_summary
    assert _query_parameters(attributes) == {}


@pytest.mark.parametrize(
    "statements,expected",
    [
        pytest.param(
            [
                {
                    "Statement": 'SELECT * FROM "orders" WHERE id = ?',
                    "Parameters": [{"S": "1"}],
                },
                {
                    "Statement": 'SELECT * FROM "orders" WHERE id = ?',
                    "Parameters": [{"S": "2"}],
                },
            ],
            {
                "name": "BATCH SELECT orders",
                SPANDATA.DB_QUERY_TEXT: 'SELECT * FROM "orders" WHERE id = ?',
                SPANDATA.DB_QUERY_SUMMARY: "BATCH SELECT orders",
                SPANDATA.DB_COLLECTION_NAME: "orders",
                SPANDATA.AWS_DYNAMODB_TABLE_NAMES: ["orders"],
                SPANDATA.DB_OPERATION_BATCH_SIZE: 2,
            },
            id="shared",
        ),
        pytest.param(
            [
                {"Statement": "SELECT * FROM orders WHERE id = '1'"},
                {"Statement": "DELETE FROM orders WHERE id = '2'"},
                {"Statement": "INSERT INTO customers VALUE {'id': '3'}"},
            ],
            {
                "name": "BatchExecuteStatement customers orders",
                SPANDATA.DB_QUERY_SUMMARY: "BatchExecuteStatement customers orders",
                SPANDATA.DB_QUERY_TEXT: "SELECT * FROM orders WHERE id = ?; "
                "DELETE FROM orders WHERE id = ?; INSERT INTO customers VALUE ?",
                SPANDATA.DB_COLLECTION_NAME: ",customers,orders,",
                SPANDATA.AWS_DYNAMODB_TABLE_NAMES: ["customers", "orders"],
                SPANDATA.DB_OPERATION_BATCH_SIZE: 3,
            },
            id="different",
        ),
        pytest.param(
            [
                {
                    "Statement": 'UPDATE "orders" SET s = ? WHERE id = ?',
                    "Parameters": [{"S": "paid"}, {"S": str(i)}],
                }
                for i in range(3)
            ]
            + [
                {"Statement": "SELECT * FROM customers WHERE id = '4'"},
                {"Statement": "SELECT * FROM customers WHERE id = '5'"},
            ],
            {
                "name": "BatchExecuteStatement customers orders",
                SPANDATA.DB_QUERY_SUMMARY: "BatchExecuteStatement customers orders",
                SPANDATA.DB_QUERY_TEXT: 'UPDATE "orders" SET s = ? WHERE id = ?; '
                "SELECT * FROM customers WHERE id = ?",
                SPANDATA.DB_COLLECTION_NAME: ",customers,orders,",
                SPANDATA.AWS_DYNAMODB_TABLE_NAMES: ["customers", "orders"],
                SPANDATA.DB_OPERATION_BATCH_SIZE: 5,
            },
            id="repeated",
        ),
        pytest.param(
            [
                {"Statement": "SELECT * FROM orders WHERE id = '1'"},
                {"Statement": "SELECT * FROM orders WHERE id = 'unterminated"},
            ],
            {
                "name": "BATCH SELECT orders",
                SPANDATA.DB_QUERY_SUMMARY: "BATCH SELECT orders",
                SPANDATA.DB_COLLECTION_NAME: "orders",
                SPANDATA.AWS_DYNAMODB_TABLE_NAMES: ["orders"],
                SPANDATA.DB_OPERATION_BATCH_SIZE: 2,
            },
            id="unsanitizable",
        ),
        pytest.param(
            [
                {
                    "Statement": 'UPDATE "orders" SET total = ? WHERE id = ?',
                    "Parameters": [{"N": "42"}, {"S": "1"}],
                }
            ],
            {
                "name": "UPDATE orders",
                SPANDATA.DB_QUERY_TEXT: 'UPDATE "orders" SET total = ? WHERE id = ?',
                SPANDATA.DB_QUERY_SUMMARY: "UPDATE orders",
                SPANDATA.DB_COLLECTION_NAME: "orders",
                SPANDATA.AWS_DYNAMODB_TABLE_NAMES: ["orders"],
                SPANDATA.DB_QUERY_PARAMETER + ".0": "42",
                SPANDATA.DB_QUERY_PARAMETER + ".1": "1",
            },
            id="single",
        ),
    ],
)
def test_batch_execute_statement(dynamodb_client, capture_items, statements, expected):
    require_botocore_model_fields(dynamodb_client, "batch_execute_statement")
    params = {"Statements": statements}

    span = _stubbed_span(
        dynamodb_client,
        capture_items,
        "batch_execute_statement",
        params,
        {"Responses": []},
    )

    expected = dict(expected)
    assert span["name"] == expected.pop("name")
    attributes = span["attributes"]
    for key in (
        SPANDATA.DB_QUERY_TEXT,
        SPANDATA.DB_QUERY_SUMMARY,
        SPANDATA.DB_COLLECTION_NAME,
        SPANDATA.AWS_DYNAMODB_TABLE_NAMES,
        SPANDATA.DB_OPERATION_BATCH_SIZE,
    ):
        assert attributes.get(key) == expected.get(key)
    assert {
        key: value
        for key, value in attributes.items()
        if key.startswith(SPANDATA.DB_QUERY_PARAMETER)
    } == {
        key: value
        for key, value in expected.items()
        if key.startswith(SPANDATA.DB_QUERY_PARAMETER)
    }


@pytest.mark.parametrize(
    "responses,expected_error_type",
    [
        pytest.param([{"TableName": "orders"}, {"TableName": "orders"}], None, id="ok"),
        pytest.param(
            [
                {"TableName": "orders"},
                {"Error": {"Code": "ConditionalCheckFailed", "Message": "failed"}},
            ],
            "ConditionalCheckFailed",
            id="one-code",
        ),
        pytest.param(
            [
                {"Error": {"Code": "ConditionalCheckFailed", "Message": "failed"}},
                {"Error": {"Code": "ThrottlingError", "Message": "throttled"}},
            ],
            "aws_batch_partial_failure",
            id="different-codes",
        ),
    ],
)
def test_batch_execute_statement_errors(
    dynamodb_client, capture_items, responses, expected_error_type
):
    require_botocore_model_fields(dynamodb_client, "batch_execute_statement")
    params = {
        "Statements": [
            {
                "Statement": 'UPDATE "orders" SET s = ? WHERE id = ?',
                "Parameters": [{"S": "paid"}, {"S": str(i)}],
            }
            for i in range(2)
        ]
    }

    span = _stubbed_span(
        dynamodb_client,
        capture_items,
        "batch_execute_statement",
        params,
        {"Responses": responses},
    )

    assert span["status"] == ("ok" if expected_error_type is None else "error")
    assert span["attributes"].get(SPANDATA.ERROR_TYPE) == expected_error_type


def test_execute_transaction(dynamodb_client, capture_items):
    require_botocore_model_fields(dynamodb_client, "execute_transaction")
    params = {
        "TransactStatements": [
            {
                "Statement": "EXISTS(SELECT * FROM \"customers\" WHERE id = '1')",
            },
            {
                "Statement": "INSERT INTO \"orders\" VALUE {'id': ?}",
                "Parameters": [{"S": "2"}],
            },
        ]
    }

    span = _stubbed_span(
        dynamodb_client, capture_items, "execute_transaction", params, {}
    )

    assert span["name"] == "ExecuteTransaction customers orders"
    attributes = span["attributes"]
    assert attributes[SPANDATA.DB_QUERY_TEXT] == (
        'EXISTS(SELECT * FROM "customers" WHERE id = ?); INSERT INTO "orders" VALUE {\'id\': ?}'
    )
    assert attributes[SPANDATA.AWS_DYNAMODB_TABLE_NAMES] == ["customers", "orders"]
    assert attributes[SPANDATA.DB_OPERATION_BATCH_SIZE] == 2
    assert (
        attributes[SPANDATA.DB_QUERY_SUMMARY] == "ExecuteTransaction customers orders"
    )
    assert attributes[SPANDATA.DB_COLLECTION_NAME] == ",customers,orders,"
    assert _query_parameters(attributes) == {}


@pytest.mark.parametrize(
    "options,expected",
    [
        pytest.param({"db_query_source_threshold_ms": 0}, True, id="enabled"),
        pytest.param(
            {"db_query_source_threshold_ms": 0, "enable_db_query_source": False},
            False,
            id="disabled",
        ),
        pytest.param({"db_query_source_threshold_ms": 60_000}, False, id="fast-call"),
    ],
)
def test_query_source(dynamodb_client, sentry_init, capture_items, options, expected):
    sentry_init(traces_sample_rate=1.0, integrations=[Boto3Integration()], **options)

    span = _stubbed_span(
        dynamodb_client,
        capture_items,
        "get_item",
        {"TableName": "orders", "Key": {"id": {"S": "1"}}},
        {},
    )

    attributes = span["attributes"]
    if expected:
        # the first in-app frame: the lambda in `_stubbed_span()` that calls the client.
        assert attributes[SPANDATA.CODE_FILE_PATH].endswith("test_dynamodb.py")
        assert attributes[SPANDATA.CODE_FUNCTION] == "<lambda>"
        assert SPANDATA.CODE_LINE_NUMBER in attributes
        assert SPANDATA.CODE_NAMESPACE in attributes
    else:
        assert SPANDATA.CODE_FILE_PATH not in attributes
        assert SPANDATA.CODE_FUNCTION not in attributes


def test_database_query_data_disabled(dynamodb_client, sentry_init, capture_items):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        data_collection={"database_query_data": False},
    )
    require_botocore_model_fields(dynamodb_client, "execute_statement")
    statement = 'SELECT * FROM "orders" WHERE id = ?'

    statement_span = _stubbed_span(
        dynamodb_client,
        capture_items,
        "execute_statement",
        {"Statement": statement, "Parameters": [{"S": "private-id"}]},
        {"Items": []},
    )
    put_span = _stubbed_span(
        dynamodb_client,
        capture_items,
        "put_item",
        {
            "TableName": "orders",
            "Item": {"id": {"S": "private-id"}},
            "ReturnItemCollectionMetrics": "SIZE",
        },
        {
            "ItemCollectionMetrics": {
                "ItemCollectionKey": {"id": {"S": "private-id"}},
                "SizeEstimateRangeGB": [0.0, 1.0],
            }
        },
    )

    # the parameterized text and summary aren't database content.
    assert statement_span["attributes"][SPANDATA.DB_QUERY_TEXT] == statement
    assert statement_span["attributes"][SPANDATA.DB_QUERY_SUMMARY] == "SELECT orders"
    assert _query_parameters(statement_span["attributes"]) == {}
    # the normalized query holds no values, so it's set either way.
    assert (
        statement_span["attributes"][SPANDATA.SENTRY_NORMALIZED_DB_QUERY]
        == 'SELECT * FROM "orders" WHERE id = %s'
    )
    assert (
        put_span["attributes"][SPANDATA.SENTRY_NORMALIZED_DB_QUERY] == "PutItem orders"
    )
    assert SPANDATA.AWS_DYNAMODB_ITEM_COLLECTION_METRICS not in put_span["attributes"]
    assert "private-id" not in json.dumps([statement_span, put_span])
