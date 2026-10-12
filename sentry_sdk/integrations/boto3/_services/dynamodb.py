import json
import re
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.integrations.boto3._utils import (
    _extract_attributes,
    _get_aws_arn_attributes,
)
from sentry_sdk.integrations.boto3.consts import BATCH_PARTIAL_FAILURE_ERROR_TYPE
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext
    from sentry_sdk.integrations.boto3._utils import _AttributeSpec

# https://opentelemetry.io/docs/specs/semconv/db/dynamodb/
_DB_SYSTEM_NAME = "aws.dynamodb"

# dynamodb request attributes that are extracted when present, regardless of operation.
# https://opentelemetry.io/docs/specs/semconv/db/dynamodb/
_REQUEST_ATTRIBUTES: "Sequence[_AttributeSpec]" = (
    ("AttributesToGet", SPANDATA.AWS_DYNAMODB_ATTRIBUTES_TO_GET),
    ("ConsistentRead", SPANDATA.AWS_DYNAMODB_CONSISTENT_READ),
    ("ExclusiveStartTableName", SPANDATA.AWS_DYNAMODB_EXCLUSIVE_START_TABLE),
    ("IndexName", SPANDATA.AWS_DYNAMODB_INDEX_NAME),
    ("Limit", SPANDATA.AWS_DYNAMODB_LIMIT),
    ("ProjectionExpression", SPANDATA.AWS_DYNAMODB_PROJECTION),
    ("ScanIndexForward", SPANDATA.AWS_DYNAMODB_SCAN_FORWARD),
    ("Segment", SPANDATA.AWS_DYNAMODB_SEGMENT),
    ("Select", SPANDATA.AWS_DYNAMODB_SELECT),
    ("TotalSegments", SPANDATA.AWS_DYNAMODB_TOTAL_SEGMENTS),
)

# dynamodb request lists whose entries are each serialized to a json string.
_REQUEST_JSON_LIST_ATTRIBUTES: "Sequence[_AttributeSpec]" = (
    ("AttributeDefinitions", SPANDATA.AWS_DYNAMODB_ATTRIBUTE_DEFINITIONS),
    ("GlobalSecondaryIndexes", SPANDATA.AWS_DYNAMODB_GLOBAL_SECONDARY_INDEXES),
    (
        "GlobalSecondaryIndexUpdates",
        SPANDATA.AWS_DYNAMODB_GLOBAL_SECONDARY_INDEX_UPDATES,
    ),
    ("LocalSecondaryIndexes", SPANDATA.AWS_DYNAMODB_LOCAL_SECONDARY_INDEXES),
)

_RESPONSE_ATTRIBUTES: "Sequence[_AttributeSpec]" = (
    ("Count", SPANDATA.AWS_DYNAMODB_COUNT),
    ("ScannedCount", SPANDATA.AWS_DYNAMODB_SCANNED_COUNT),
)

# a partiql statement addresses one table, optionally double-quoted. a select can
# read from an index of that table (`"orders"."by-customer"`).
# https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/ql-reference.select.html
_PARTIQL_TABLE = r'(?:"(?P<quoted>[^"]+)"|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))'
_PARTIQL_INDEX = (
    r'(?:\s*\.\s*(?:"(?P<quoted_index>[^"]+)"|(?P<bare_index>[A-Za-z_][A-Za-z0-9_]*)))?'
)

# maps the query summary operation to the statement forms that name their table.
# `EXISTS` only occurs in `ExecuteTransaction`.
# https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/ql-reference.statements.html
# https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/ql-functions.exists.html
_PARTIQL_STATEMENTS = (
    (
        "SELECT",
        re.compile(
            r"\s*SELECT\b.*?\bFROM\s+" + _PARTIQL_TABLE + _PARTIQL_INDEX, re.I | re.S
        ),
    ),
    ("INSERT", re.compile(r"\s*INSERT\s+INTO\s+" + _PARTIQL_TABLE, re.I)),
    ("UPDATE", re.compile(r"\s*UPDATE\s+" + _PARTIQL_TABLE, re.I)),
    ("DELETE", re.compile(r"\s*DELETE\s+FROM\s+" + _PARTIQL_TABLE, re.I)),
    (
        "EXISTS",
        re.compile(
            r"\s*EXISTS\s*\(\s*SELECT\b.*?\bFROM\s+" + _PARTIQL_TABLE + _PARTIQL_INDEX,
            re.I | re.S,
        ),
    ),
)

# identifiers (incl. keywords) are matched as a whole, so digits inside them never
# start a number.
_PARTIQL_TOKEN = re.compile(
    r"(?P<word>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<number>(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
)
_PARTIQL_LITERAL_KEYWORDS = ("true", "false", "null", "missing")

_JSON_SEPARATORS = (",", ":")


def _get_table_name(table: str) -> str:
    # `TableName` and the `RequestItems` keys accept a table name or a table ARN
    # (`arn:aws:dynamodb:<region>:<account>:table/<name>`); table names can't contain ":".
    # https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_GetItem.html#DDB-GetItem-request-TableName
    return table.rsplit(":table/", 1)[-1]


def _get_table_attributes(tables: "Iterable[str]") -> "Attributes":
    table_names = list(dict.fromkeys(_get_table_name(table) for table in tables))
    attributes: "Attributes" = {}
    if table_names:
        attributes[SPANDATA.AWS_DYNAMODB_TABLE_NAMES] = table_names
    if len(table_names) == 1:
        attributes[SPANDATA.DB_COLLECTION_NAME] = table_names[0]
    elif len(table_names) > 1:
        # relay stores the tables it infers from a query as a sorted, comma-wrapped list
        # (`,orders,users,`) and keeps a collection name set by the sdk. use the same format
        # (keeping the case, table names are case-sensitive), so multi-table calls can be
        # filtered by table like other db spans and relay doesn't infer one from the query.
        # https://github.com/getsentry/relay/blob/master/relay-event-normalization/src/normalize/span/tag_extraction.rs
        attributes[SPANDATA.DB_COLLECTION_NAME] = ",{},".format(
            ",".join(sorted(table_names))
        )
    return attributes


def _get_request_tables(params: "Dict[str, Any]") -> "List[str]":
    tables = []
    if "TableName" in params:
        tables.append(params["TableName"])
    if "RequestItems" in params:
        tables.extend(params["RequestItems"])
    if "TransactItems" in params:
        # each transaction item has exactly one request, e.g. {"Put": {"TableName": ...}}.
        # https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_TransactWriteItem.html
        for item in params["TransactItems"]:
            tables.extend(request["TableName"] for request in item.values())
    return tables


def _get_batch_size(ctx: "AwsCallContext") -> "Optional[int]":
    if ctx.operation_name == "BatchGetItem":
        return sum(
            len(request["Keys"]) for request in ctx.params["RequestItems"].values()
        )
    if ctx.operation_name == "BatchWriteItem":
        return sum(len(requests) for requests in ctx.params["RequestItems"].values())
    if ctx.operation_name in ("TransactGetItems", "TransactWriteItems"):
        return len(ctx.params["TransactItems"])
    if ctx.operation_name == "BatchExecuteStatement":
        return len(ctx.params["Statements"])
    if ctx.operation_name == "ExecuteTransaction":
        return len(ctx.params["TransactStatements"])
    return None


def _get_partiql_statements(
    ctx: "AwsCallContext",
) -> "Optional[List[Dict[str, Any]]]":
    # every statement entry holds a `Statement` and optional `Parameters`.
    # https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_BatchStatementRequest.html
    if ctx.operation_name == "ExecuteStatement":
        return [ctx.params]
    if ctx.operation_name == "BatchExecuteStatement":
        return ctx.params["Statements"]
    if ctx.operation_name == "ExecuteTransaction":
        return ctx.params["TransactStatements"]
    return None


def _parse_partiql_statement(
    statement: str,
) -> "Optional[Tuple[str, str, Optional[str]]]":
    """Return the query summary operation, table and index of a statement, or `None` if not recognized."""
    statement = _strip_partiql_comments(statement)
    for operation, pattern in _PARTIQL_STATEMENTS:
        match = pattern.match(statement)
        if match is not None:
            groups = match.groupdict()
            return (
                operation,
                groups["quoted"] or groups["bare"],
                groups.get("quoted_index") or groups.get("bare_index"),
            )
    return None


def _find_quote_end(statement: str, start: int) -> "Optional[int]":
    """Return the index after the quoted token at `start`, or `None` if it's unterminated."""
    # partiql escapes a quote inside a quoted token by doubling it, e.g. 'it''s'.
    quote = statement[start]
    end = statement.find(quote, start + 1)
    while end != -1 and statement.startswith(quote, end + 1):
        end = statement.find(quote, end + 2)
    return None if end == -1 else end + 1


def _strip_partiql_comments(statement: str) -> str:
    """Remove `--` line and `/* */` block comments outside quoted tokens, each with its surrounding whitespace."""
    # comments are free text that may contain literals (or an apostrophe that would
    # look like an unterminated string), and they can precede the statement itself.
    # https://partiql.org/partiql-lang/
    parts = []
    chunk_start = 0
    index = 0
    while index < len(statement):
        char = statement[index]
        if char in "'\"`":
            end = _find_quote_end(statement, index)
            if end is None:
                # an unterminated token is left to the sanitizer, which rejects it.
                break
            index = end
            continue

        if statement.startswith("--", index):
            comment_end = statement.find("\n", index)
        elif statement.startswith("/*", index):
            comment_end = statement.find("*/", index + 2)
            if comment_end != -1:
                comment_end += 2
        else:
            index += 1
            continue

        parts.append(statement[chunk_start:index].rstrip())
        parts.append(" ")
        index = len(statement) if comment_end == -1 else comment_end
        while index < len(statement) and statement[index].isspace():
            index += 1
        chunk_start = index

    parts.append(statement[chunk_start:])
    return "".join(parts).strip()


def _find_collection_end(statement: str, start: int) -> "Optional[int]":
    """Return the index after the tuple, list or bag literal at `start`, or `None` if it's unterminated."""
    depth = 0
    index = start
    while index < len(statement):
        char = statement[index]
        if char in "'\"`":
            end = _find_quote_end(statement, index)
            if end is None:
                return None
            index = end
            continue

        if statement.startswith("<<", index):
            depth += 1
            index += 2
        elif statement.startswith(">>", index):
            depth -= 1
            index += 2
        elif char in "{[":
            depth += 1
            index += 1
        elif char in "}]":
            depth -= 1
            index += 1
        else:
            index += 1
            continue

        if depth == 0:
            return index
    return None


def _sanitize_partiql(statement: str) -> "Optional[str]":
    """Replace every literal in a statement with `?`, or return `None` if the statement can't be tokenized."""
    # https://partiql.org/partiql-lang/#sec:literals
    # https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/ql-reference.data-types.html
    sanitized = []
    # the last two words, to keep the `IS [NOT] NULL` and `IS [NOT] MISSING` predicates.
    previous_words = ("", "")
    index = 0
    while index < len(statement):
        char = statement[index]
        end: "Optional[int]"
        if char in "'\"`":
            # strings ('…') and ion values (`…`) are literals; double quotes delimit identifiers.
            end = _find_quote_end(statement, index)
        elif (
            char == "{"
            or statement.startswith("<<", index)
            # `[` directly after a path (`tags[0]`) is an index, otherwise a list literal.
            or (
                char == "["
                and not (
                    index > 0
                    and (
                        statement[index - 1].isalnum() or statement[index - 1] in '_"]'
                    )
                )
            )
        ):
            end = _find_collection_end(statement, index)
        else:
            match = _PARTIQL_TOKEN.match(statement, index)
            if match is None:
                sanitized.append(char)
                index += 1
            elif match.lastgroup == "number":
                sanitized.append("?")
                index = match.end()
            else:
                word = match.group().lower()
                is_literal = word in _PARTIQL_LITERAL_KEYWORDS and not (
                    previous_words[1] == "is" or previous_words == ("is", "not")
                )
                sanitized.append("?" if is_literal else match.group())
                previous_words = (previous_words[1], word)
                index = match.end()
            continue

        if end is None:
            return None
        sanitized.append(statement[index:end] if char == '"' else "?")
        index = end
    return "".join(sanitized)


def _get_query_text(statements: "List[Dict[str, Any]]") -> "Optional[str]":
    """Return the shared query text, the distinct texts joined with "; " if they differ, or `None` if one can't be sanitized."""
    query_texts = []
    for statement in statements:
        # parameterized statements carry their values in `Parameters`, so they're used as provided.
        if statement.get("Parameters"):
            query_text: "Optional[str]" = statement["Statement"]
        else:
            query_text = _sanitize_partiql(
                _strip_partiql_comments(statement["Statement"])
            )
        if query_text is None:
            return None
        query_texts.append(query_text)

    # a batch of up to 100 statements usually repeats a few; repeating them in the text
    # would only make it longer and unique per batch size.
    return "; ".join(dict.fromkeys(query_texts))


def _has_binary(attribute_value: "Dict[str, Any]") -> bool:
    # an `AttributeValue` has exactly one member, its data type.
    # https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_AttributeValue.html
    ((data_type, value),) = attribute_value.items()
    if data_type == "L":
        return any(_has_binary(element) for element in value)
    if data_type == "M":
        return any(_has_binary(element) for element in value.values())
    return data_type in ("B", "BS")


def _get_parameter_value(parameter: "Dict[str, Any]") -> "Optional[str]":
    if _has_binary(parameter):
        return None

    ((data_type, value),) = parameter.items()
    if data_type in ("S", "N"):
        return value
    if data_type == "BOOL":
        return "true" if value else "false"
    if data_type == "NULL":
        return "null"
    if data_type in ("SS", "NS"):
        return json.dumps(value, separators=_JSON_SEPARATORS)
    # `L` and `M` keep their data type, e.g. {"M":{"id":{"S":"1"}}}.
    return json.dumps(parameter, separators=_JSON_SEPARATORS)


def _get_partiql_attributes(statements: "List[Dict[str, Any]]") -> "Attributes":
    """
    Attributes for `ExecuteStatement`, `BatchExecuteStatement` and `ExecuteTransaction`.
    A batch or transaction with one statement is described like a single statement.
    https://opentelemetry.io/docs/specs/semconv/db/database-spans/#batch-operations
    """
    attributes: "Attributes" = {}

    query_text = _get_query_text(statements)
    if query_text is not None:
        attributes[SPANDATA.DB_QUERY_TEXT] = query_text

    parsed_statements = [
        _parse_partiql_statement(statement["Statement"]) for statement in statements
    ]
    recognized_statements = [
        parsed for parsed in parsed_statements if parsed is not None
    ]
    all_recognized = len(recognized_statements) == len(statements)
    # a batch only has a summary when every statement shares the same one, prefixed with
    # `BATCH ` so it's named apart from a single statement. otel uses `BATCH` alone for
    # differing statements; they keep no summary, so they're named after their tables.
    # https://opentelemetry.io/docs/specs/semconv/registry/attributes/db/#db-query-summary
    summaries = {
        f"{operation} {table}" for operation, table, _ in recognized_statements
    }
    if all_recognized and len(summaries) == 1:
        summary = summaries.pop()
        attributes[SPANDATA.DB_QUERY_SUMMARY] = (
            f"BATCH {summary}" if len(statements) > 1 else summary
        )

    # like `IndexName` of `Query` and `Scan`, when every statement reads the same index.
    indexes = {(table, index) for _, table, index in recognized_statements}
    if all_recognized and len(indexes) == 1:
        ((_, index),) = indexes
        if index is not None:
            attributes[SPANDATA.AWS_DYNAMODB_INDEX_NAME] = index

    attributes.update(
        _get_table_attributes(table for _, table, _ in recognized_statements)
    )

    # parameters are database content, and only set for a single statement.
    # https://opentelemetry.io/docs/specs/semconv/db/database-spans/#common-attributes
    if (
        len(statements) == 1
        and sentry_sdk.get_client().options["data_collection"]["database_query_data"]
    ):
        for index, parameter in enumerate(statements[0].get("Parameters", ())):
            value = _get_parameter_value(parameter)
            if value is not None:
                attributes[f"{SPANDATA.DB_QUERY_PARAMETER}.{index}"] = value

    return attributes


class _DynamoDBExtension(_ServiceExtension):
    __slots__ = ()

    def get_span_op(self, ctx: "AwsCallContext") -> "Optional[str]":
        return OP.DB

    def get_span_name(
        self, ctx: "AwsCallContext", attributes: "Attributes"
    ) -> "Optional[str]":
        # sentry's ordered db name templates; dynamodb always sets `db.operation.name`
        # and never `db.stored_procedure.name` or `db.namespace`.
        # https://github.com/getsentry/sentry-conventions/blob/main/model/name/db.json
        if SPANDATA.DB_QUERY_SUMMARY in attributes:
            return str(attributes[SPANDATA.DB_QUERY_SUMMARY])

        # a multi-table call is named after its tables rather than its comma-wrapped
        # `db.collection.name`, sorted so the name doesn't depend on the request order.
        table_names = attributes.get(SPANDATA.AWS_DYNAMODB_TABLE_NAMES)
        if isinstance(table_names, list) and table_names:
            return "{} {}".format(
                ctx.operation_name, ", ".join(sorted(str(t) for t in table_names))
            )

        # calls without a table. `server.address` is still the configured endpoint here;
        # botocore may resolve an account-based one (`<account>.ddb.<region>.amazonaws.com`)
        # later, which keeps the name stable and free of account ids.
        server_address = attributes.get(SPANDATA.SERVER_ADDRESS)
        server_port = attributes.get(SPANDATA.SERVER_PORT)
        if server_address is None or server_port is None:
            return None
        return f"{ctx.operation_name} {server_address}:{server_port}"

    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        attributes: "Attributes" = {
            SPANDATA.DB_SYSTEM_NAME: _DB_SYSTEM_NAME,
            SPANDATA.DB_OPERATION_NAME: ctx.operation_name,
        }
        attributes.update(_extract_attributes(ctx.params, _REQUEST_ATTRIBUTES))
        with capture_internal_exceptions():
            attributes.update(
                _get_aws_arn_attributes(
                    (
                        ctx.params.get("ResourceArn"),
                        ctx.params.get("TableArn"),
                        ctx.params.get("TableName"),
                    ),
                    ("dynamodb",),
                )
            )

        for param, attribute in _REQUEST_JSON_LIST_ATTRIBUTES:
            if param in ctx.params:
                attributes[attribute] = [
                    json.dumps(entry, separators=_JSON_SEPARATORS)
                    for entry in ctx.params[param]
                ]

        if "ProvisionedThroughput" in ctx.params:
            # capacity units are integers in the request, but defined as doubles.
            # https://opentelemetry.io/docs/specs/semconv/registry/attributes/aws/#aws-dynamodb-provisioned-read-capacity
            throughput = ctx.params["ProvisionedThroughput"]
            attributes[SPANDATA.AWS_DYNAMODB_PROVISIONED_READ_CAPACITY] = float(
                throughput["ReadCapacityUnits"]
            )
            attributes[SPANDATA.AWS_DYNAMODB_PROVISIONED_WRITE_CAPACITY] = float(
                throughput["WriteCapacityUnits"]
            )

        with capture_internal_exceptions():
            statements = _get_partiql_statements(ctx)
            if statements is None:
                attributes.update(
                    _get_table_attributes(_get_request_tables(ctx.params))
                )
            else:
                attributes.update(_get_partiql_attributes(statements))

        # a single operation is not a batch.
        # https://opentelemetry.io/docs/specs/semconv/registry/attributes/db/#db-operation-batch-size
        batch_size = _get_batch_size(ctx)
        if batch_size is not None and batch_size >= 2:
            attributes[SPANDATA.DB_OPERATION_BATCH_SIZE] = batch_size

        return attributes

    def get_response_attributes(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "Attributes":
        attributes: "Attributes" = _extract_attributes(response, _RESPONSE_ATTRIBUTES)

        if "TableNames" in response:
            attributes[SPANDATA.AWS_DYNAMODB_TABLE_COUNT] = len(response["TableNames"])

        if ctx.operation_name == "BatchExecuteStatement":
            # failed statements are reported per entry in a successful response, not raised.
            # https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_BatchStatementResponse.html
            error_codes = {
                entry["Error"].get("Code") or BATCH_PARTIAL_FAILURE_ERROR_TYPE
                for entry in response.get("Responses", ())
                if entry.get("Error")
            }
            if error_codes:
                attributes[SPANDATA.ERROR_TYPE] = (
                    error_codes.pop()
                    if len(error_codes) == 1
                    else BATCH_PARTIAL_FAILURE_ERROR_TYPE
                )

        if "ConsumedCapacity" in response:
            # single-request operations return one object, batch and transaction operations a list.
            # https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_BatchGetItem.html#DDB-BatchGetItem-response-ConsumedCapacity
            consumed_capacity = response["ConsumedCapacity"]
            if not isinstance(consumed_capacity, list):
                consumed_capacity = [consumed_capacity]
            attributes[SPANDATA.AWS_DYNAMODB_CONSUMED_CAPACITY] = [
                json.dumps(entry, separators=_JSON_SEPARATORS)
                for entry in consumed_capacity
            ]

        if (
            "ItemCollectionMetrics" in response
            and sentry_sdk.get_client().options["data_collection"][
                "database_query_data"
            ]
        ):
            # `ItemCollectionKey` holds partition key values, i.e. database content.
            # https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_ItemCollectionMetrics.html
            try:
                attributes[SPANDATA.AWS_DYNAMODB_ITEM_COLLECTION_METRICS] = json.dumps(
                    response["ItemCollectionMetrics"], separators=_JSON_SEPARATORS
                )
            except TypeError:
                # binary partition keys are parsed to `bytes`, which aren't json serializable;
                # omit them like binary query parameters.
                pass

        return attributes
