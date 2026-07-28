from typing import AsyncGenerator, Optional

import pytest

import sentry_sdk

strawberry = pytest.importorskip("strawberry")
pytest.importorskip("fastapi")
pytest.importorskip("flask")


from fastapi import FastAPI
from fastapi.testclient import TestClient
from flask import Flask
from strawberry.fastapi import GraphQLRouter
from strawberry.flask.views import GraphQLView

from sentry_sdk.consts import OP
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.flask import FlaskIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.integrations.strawberry import (
    SentryAsyncExtension,
    SentrySyncExtension,
    StrawberryIntegration,
)
from tests.conftest import ApproxDict

try:
    from strawberry.extensions.tracing import (
        SentryTracingExtension,
        SentryTracingExtensionSync,
    )
except ImportError:
    SentryTracingExtension = None
    SentryTracingExtensionSync = None

parameterize_strawberry_test = pytest.mark.parametrize(
    "client_factory,async_execution,framework_integrations",
    (
        (
            "async_app_client_factory",
            True,
            [FastApiIntegration(), StarletteIntegration()],
        ),
        ("sync_app_client_factory", False, [FlaskIntegration()]),
    ),
)


@strawberry.type
class Query:
    @strawberry.field
    def hello(self) -> str:
        return "Hello World"

    @strawberry.field
    def error(self) -> int:
        return 1 / 0


@strawberry.type
class QueryWithArg:
    @strawberry.field
    def fail(self, value: str) -> str:
        raise RuntimeError("oh no!")


@strawberry.type
class QueryWithCaptureException:
    @strawberry.field
    def echo(self, value: str) -> str:
        sentry_sdk.capture_exception(RuntimeError("boom"))
        return value

    @strawberry.field
    def hello(self) -> str:
        sentry_sdk.capture_exception(RuntimeError("boom"))
        return "Hello World"


@strawberry.type
class Mutation:
    @strawberry.mutation
    def change(self, attribute: str) -> str:
        return attribute


@strawberry.type
class Message:
    content: str


@strawberry.type
class Subscription:
    @strawberry.subscription
    async def message_added(self) -> Optional[AsyncGenerator[Message, None]]:
        message = Message(content="Hello, world!")
        yield message


@pytest.fixture
def async_app_client_factory():
    def create_app(schema):
        async_app = FastAPI()
        async_app.include_router(GraphQLRouter(schema), prefix="/graphql")
        return TestClient(async_app)

    return create_app


@pytest.fixture
def sync_app_client_factory():
    def create_app(schema):
        sync_app = Flask(__name__)
        sync_app.add_url_rule(
            "/graphql",
            view_func=GraphQLView.as_view("graphql_view", schema=schema),
        )
        return sync_app.test_client()

    return create_app


def test_async_execution_uses_async_extension(sentry_init):
    sentry_init(integrations=[StrawberryIntegration(async_execution=True)])

    schema = strawberry.Schema(Query)
    assert SentryAsyncExtension in schema.extensions
    assert SentrySyncExtension not in schema.extensions


def test_sync_execution_uses_sync_extension(sentry_init):
    sentry_init(integrations=[StrawberryIntegration(async_execution=False)])

    schema = strawberry.Schema(Query)
    assert SentrySyncExtension in schema.extensions
    assert SentryAsyncExtension not in schema.extensions


def test_use_sync_extension_if_not_specified(sentry_init):
    sentry_init(integrations=[StrawberryIntegration()])
    schema = strawberry.Schema(Query)
    assert SentrySyncExtension in schema.extensions
    assert SentryAsyncExtension not in schema.extensions


@pytest.mark.skipif(
    SentryTracingExtension is None,
    reason="SentryTracingExtension no longer available in this Strawberry version",
)
def test_replace_existing_sentry_async_extension(sentry_init):
    sentry_init(integrations=[StrawberryIntegration()])

    schema = strawberry.Schema(Query, extensions=[SentryTracingExtension])
    assert SentryTracingExtension not in schema.extensions
    assert SentrySyncExtension not in schema.extensions
    assert SentryAsyncExtension in schema.extensions


@pytest.mark.skipif(
    SentryTracingExtensionSync is None,
    reason="SentryTracingExtensionSync no longer available in this Strawberry version",
)
def test_replace_existing_sentry_sync_extension(sentry_init):
    sentry_init(integrations=[StrawberryIntegration()])

    schema = strawberry.Schema(Query, extensions=[SentryTracingExtensionSync])
    assert SentryTracingExtensionSync not in schema.extensions
    assert SentryAsyncExtension not in schema.extensions
    assert SentrySyncExtension in schema.extensions


@parameterize_strawberry_test
def test_capture_request_if_available_and_send_pii_is_on(
    request,
    sentry_init,
    capture_events,
    client_factory,
    async_execution,
    framework_integrations,
):
    sentry_init(
        send_default_pii=True,
        integrations=[
            StrawberryIntegration(async_execution=async_execution),
        ]
        + framework_integrations,
    )
    events = capture_events()

    schema = strawberry.Schema(Query)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    query = "query ErrorQuery { error }"
    client.post("/graphql", json={"query": query, "operationName": "ErrorQuery"})

    assert len(events) == 1

    (error_event,) = events

    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "strawberry"
    assert error_event["request"]["api_target"] == "graphql"
    assert error_event["request"]["data"] == {
        "query": query,
        "operationName": "ErrorQuery",
    }
    assert error_event["contexts"]["response"] == {
        "data": {
            "data": None,
            "errors": [
                {
                    "message": "division by zero",
                    "locations": [{"line": 1, "column": 20}],
                    "path": ["error"],
                }
            ],
        }
    }
    assert len(error_event["breadcrumbs"]["values"]) == 1
    assert error_event["breadcrumbs"]["values"][0]["category"] == "graphql.operation"
    assert error_event["breadcrumbs"]["values"][0]["data"] == {
        "operation_name": "ErrorQuery",
        "operation_type": "query",
    }


@parameterize_strawberry_test
def test_do_not_capture_request_if_send_pii_is_off(
    request,
    sentry_init,
    capture_events,
    client_factory,
    async_execution,
    framework_integrations,
):
    sentry_init(
        integrations=[
            StrawberryIntegration(async_execution=async_execution),
        ]
        + framework_integrations,
    )
    events = capture_events()

    schema = strawberry.Schema(Query)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    query = "query ErrorQuery { error }"
    client.post("/graphql", json={"query": query, "operationName": "ErrorQuery"})

    assert len(events) == 1

    (error_event,) = events
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "strawberry"
    assert "data" not in error_event["request"]
    assert "response" not in error_event["contexts"]

    assert len(error_event["breadcrumbs"]["values"]) == 1
    assert error_event["breadcrumbs"]["values"][0]["category"] == "graphql.operation"
    assert error_event["breadcrumbs"]["values"][0]["data"] == {
        "operation_name": "ErrorQuery",
        "operation_type": "query",
    }


@parameterize_strawberry_test
@pytest.mark.parametrize(
    "data_collection,send_default_pii,expect_api_target",
    [
        pytest.param(
            {"graphql": {"document": True}},
            None,
            True,
            id="document_on_sets_api_target",
        ),
        pytest.param(
            {"graphql": {"document": False}},
            None,
            False,
            id="document_off_omits_api_target",
        ),
        pytest.param(
            {"graphql": {"document": False}},
            True,
            False,
            id="data_collection_takes_precedence_over_send_default_pii_on",
        ),
        pytest.param(
            {"graphql": {"document": True}},
            False,
            True,
            id="data_collection_takes_precedence_over_send_default_pii_off",
        ),
    ],
)
def test_event_processor_data_collection(
    request,
    sentry_init,
    capture_events,
    client_factory,
    async_execution,
    framework_integrations,
    data_collection,
    send_default_pii,
    expect_api_target,
):
    init_kwargs = {
        "integrations": [StrawberryIntegration(async_execution=async_execution)]
        + framework_integrations,
        "_experiments": {"data_collection": data_collection},
    }
    if send_default_pii is not None:
        init_kwargs["send_default_pii"] = send_default_pii
    sentry_init(**init_kwargs)
    events = capture_events()

    schema = strawberry.Schema(QueryWithArg)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    query = "query ErrorQuery($value: String!) { fail(value: $value) }"
    client.post(
        "/graphql",
        json={
            "query": query,
            "operationName": "ErrorQuery",
            "variables": {"value": "boom"},
        },
    )

    assert len(events) == 1

    (error_event,) = events
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "strawberry"

    # request.data comes from the framework integration and must not be
    # overwritten by the strawberry integration
    assert error_event["request"]["data"] == {
        "query": query,
        "operationName": "ErrorQuery",
        "variables": {"value": "boom"},
    }

    if expect_api_target:
        assert error_event["request"]["api_target"] == "graphql"
    else:
        assert "api_target" not in error_event.get("request", {})


@pytest.mark.parametrize(
    "data_collection,expect_query,expect_variables",
    [
        pytest.param(
            {"graphql": {"document": True, "variables": True}},
            True,
            True,
            id="document_and_variables_on",
        ),
        pytest.param(
            {"graphql": {"document": False, "variables": True}},
            False,
            True,
            id="document_off_variables_on",
        ),
        pytest.param(
            {"graphql": {"document": True, "variables": False}},
            True,
            False,
            id="document_on_variables_off",
        ),
        pytest.param(
            {"graphql": {"document": False, "variables": False}},
            False,
            False,
            id="document_and_variables_off",
        ),
        pytest.param(
            {"user_info": False},
            True,
            True,
            id="omitted_graphql_config_uses_spec_defaults",
        ),
    ],
)
def test_request_data_collection_no_framework(
    sentry_init, capture_events, data_collection, expect_query, expect_variables
):
    # capturing an event during direct schema execution (without a web
    # framework integration) exercises the request data collection through
    # the integration's public event-processing path
    sentry_init(
        integrations=[StrawberryIntegration()],
        _experiments={"data_collection": data_collection},
    )
    events = capture_events()

    schema = strawberry.Schema(QueryWithCaptureException)

    query = "query EchoQuery($value: String!) { echo(value: $value) }"
    schema.execute_sync(
        query,
        variable_values={"value": "boom"},
        operation_name="EchoQuery",
    )

    assert len(events) == 1

    (error_event,) = events
    assert error_event["exception"]["values"][0]["value"] == "boom"

    request_data = error_event["request"]["data"]

    # operationName is always collected when data collection is enabled,
    # regardless of the document setting
    assert request_data.get("operationName") == "EchoQuery"

    if expect_query:
        assert error_event["request"]["api_target"] == "graphql"
        assert request_data.get("query") == query
    else:
        assert "api_target" not in error_event["request"]
        assert "query" not in request_data

    if expect_variables:
        assert request_data.get("variables") == {"value": "boom"}
    else:
        assert "variables" not in request_data


def test_request_data_collection_no_variables(sentry_init, capture_events):
    sentry_init(
        integrations=[StrawberryIntegration()],
        _experiments={
            "data_collection": {"graphql": {"document": True, "variables": True}}
        },
    )
    events = capture_events()

    schema = strawberry.Schema(QueryWithCaptureException)

    schema.execute_sync(
        "query HelloQuery { hello }",
        operation_name="HelloQuery",
    )

    assert len(events) == 1

    (error_event,) = events
    assert error_event["request"]["data"] == {
        "query": "query HelloQuery { hello }",
        "operationName": "HelloQuery",
    }


@parameterize_strawberry_test
def test_breadcrumb_no_operation_name(
    request,
    sentry_init,
    capture_events,
    client_factory,
    async_execution,
    framework_integrations,
):
    sentry_init(
        integrations=[
            StrawberryIntegration(async_execution=async_execution),
        ]
        + framework_integrations,
    )
    events = capture_events()

    schema = strawberry.Schema(Query)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    query = "{ error }"
    client.post("/graphql", json={"query": query})

    assert len(events) == 1

    (error_event,) = events

    assert len(error_event["breadcrumbs"]["values"]) == 1
    assert error_event["breadcrumbs"]["values"][0]["category"] == "graphql.operation"
    assert error_event["breadcrumbs"]["values"][0]["data"] == {
        "operation_name": None,
        "operation_type": "query",
    }


@parameterize_strawberry_test
@pytest.mark.parametrize(
    "send_default_pii",
    [True, False],
)
@pytest.mark.parametrize("span_streaming", [True, False])
def test_capture_transaction_on_error(
    request,
    sentry_init,
    capture_events,
    capture_items,
    client_factory,
    async_execution,
    framework_integrations,
    send_default_pii,
    span_streaming,
):
    sentry_init(
        send_default_pii=send_default_pii,
        integrations=[
            StrawberryIntegration(async_execution=async_execution),
        ]
        + framework_integrations,
        traces_sample_rate=1,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming:
        items = capture_items("event", "span")
    else:
        events = capture_events()

    schema = strawberry.Schema(Query)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    query = "query ErrorQuery { error }"
    client.post("/graphql", json={"query": query, "operationName": "ErrorQuery"})

    if span_streaming:
        sentry_sdk.flush()
        error_events = [i.payload for i in items if i.type == "event"]
        spans = [i.payload for i in items if i.type == "span"]

        assert len(error_events) == 1

        assert len(spans) == 5
        parse_span, validate_span, resolve_span, query_span, segment = spans

        assert segment["is_segment"] is True
        assert segment["name"] == "ErrorQuery"
        assert segment["attributes"]["sentry.op"] == OP.GRAPHQL_QUERY

        assert query_span["attributes"]["sentry.op"] == OP.GRAPHQL_QUERY
        assert query_span["name"] == "query ErrorQuery"
        assert query_span["attributes"]["graphql.operation.type"] == "query"
        assert query_span["attributes"]["graphql.operation.name"] == "ErrorQuery"

        if send_default_pii is True:
            assert query_span["attributes"]["graphql.document"] == query
        else:
            assert "graphql.document" not in query_span["attributes"]

        assert parse_span["attributes"]["sentry.op"] == OP.GRAPHQL_PARSE
        assert parse_span["name"] == "parsing"
        assert parse_span["parent_span_id"] == query_span["span_id"]

        assert validate_span["attributes"]["sentry.op"] == OP.GRAPHQL_VALIDATE
        assert validate_span["name"] == "validation"
        assert validate_span["parent_span_id"] == query_span["span_id"]

        assert resolve_span["attributes"]["sentry.op"] == OP.GRAPHQL_RESOLVE
        assert resolve_span["name"] == "resolving Query.error"
        assert resolve_span["parent_span_id"] == query_span["span_id"]
    else:
        assert len(events) == 2
        (_, transaction_event) = events

        assert transaction_event["transaction"] == "ErrorQuery"
        assert transaction_event["contexts"]["trace"]["op"] == OP.GRAPHQL_QUERY
        assert transaction_event["spans"]

        query_spans = [
            span
            for span in transaction_event["spans"]
            if span["op"] == OP.GRAPHQL_QUERY
        ]
        assert len(query_spans) == 1, "exactly one query span expected"
        query_span = query_spans[0]
        assert query_span["description"] == "query ErrorQuery"
        assert query_span["data"]["graphql.operation.type"] == "query"
        assert query_span["data"]["graphql.operation.name"] == "ErrorQuery"
        assert query_span["data"]["graphql.resource_name"]

        if send_default_pii is True:
            assert query_span["data"]["graphql.document"] == query
        else:
            assert "graphql.document" not in query_span["data"]

        parse_spans = [
            span
            for span in transaction_event["spans"]
            if span["op"] == OP.GRAPHQL_PARSE
        ]
        assert len(parse_spans) == 1, "exactly one parse span expected"
        parse_span = parse_spans[0]
        assert parse_span["parent_span_id"] == query_span["span_id"]
        assert parse_span["description"] == "parsing"

        validate_spans = [
            span
            for span in transaction_event["spans"]
            if span["op"] == OP.GRAPHQL_VALIDATE
        ]
        assert len(validate_spans) == 1, "exactly one validate span expected"
        validate_span = validate_spans[0]
        assert validate_span["parent_span_id"] == query_span["span_id"]
        assert validate_span["description"] == "validation"

        resolve_spans = [
            span
            for span in transaction_event["spans"]
            if span["op"] == OP.GRAPHQL_RESOLVE
        ]
        assert len(resolve_spans) == 1, "exactly one resolve span expected"
        resolve_span = resolve_spans[0]
        assert resolve_span["parent_span_id"] == query_span["span_id"]
        assert resolve_span["description"] == "resolving Query.error"
        assert resolve_span["data"] == ApproxDict(
            {
                "graphql.field_name": "error",
                "graphql.parent_type": "Query",
                "graphql.field_path": "Query.error",
                "graphql.path": "error",
            }
        )


@parameterize_strawberry_test
@pytest.mark.parametrize(
    "send_default_pii",
    [True, False],
)
@pytest.mark.parametrize("span_streaming", [True, False])
def test_capture_transaction_on_success(
    request,
    sentry_init,
    capture_events,
    capture_items,
    client_factory,
    async_execution,
    framework_integrations,
    send_default_pii,
    span_streaming,
):
    sentry_init(
        integrations=[
            StrawberryIntegration(async_execution=async_execution),
        ]
        + framework_integrations,
        traces_sample_rate=1,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming:
        items = capture_items("span")
    else:
        events = capture_events()

    schema = strawberry.Schema(Query)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    query = "query GreetingQuery { hello }"
    client.post("/graphql", json={"query": query, "operationName": "GreetingQuery"})

    if span_streaming:
        sentry_sdk.flush()
        spans = [i.payload for i in items]

        assert len(spans) == 5
        parse_span, validate_span, resolve_span, query_span, segment = spans

        assert segment["is_segment"] is True
        assert segment["name"] == "GreetingQuery"
        assert segment["attributes"]["sentry.op"] == OP.GRAPHQL_QUERY

        assert query_span["attributes"]["sentry.op"] == OP.GRAPHQL_QUERY
        assert query_span["name"] == "query GreetingQuery"
        assert query_span["attributes"]["graphql.operation.type"] == "query"
        assert query_span["attributes"]["graphql.operation.name"] == "GreetingQuery"

        if send_default_pii is True:
            assert query_span["attributes"]["graphql.document"] == query
        else:
            assert "graphql.document" not in query_span["attributes"]

        assert parse_span["attributes"]["sentry.op"] == OP.GRAPHQL_PARSE
        assert parse_span["name"] == "parsing"
        assert parse_span["parent_span_id"] == query_span["span_id"]

        assert validate_span["attributes"]["sentry.op"] == OP.GRAPHQL_VALIDATE
        assert validate_span["name"] == "validation"
        assert validate_span["parent_span_id"] == query_span["span_id"]

        assert resolve_span["attributes"]["sentry.op"] == OP.GRAPHQL_RESOLVE
        assert resolve_span["name"] == "resolving Query.hello"
        assert resolve_span["parent_span_id"] == query_span["span_id"]
    else:
        assert len(events) == 1
        (transaction_event,) = events

        assert transaction_event["transaction"] == "GreetingQuery"
        assert transaction_event["contexts"]["trace"]["op"] == OP.GRAPHQL_QUERY
        assert transaction_event["spans"]

        query_spans = [
            span
            for span in transaction_event["spans"]
            if span["op"] == OP.GRAPHQL_QUERY
        ]
        assert len(query_spans) == 1, "exactly one query span expected"
        query_span = query_spans[0]
        assert query_span["description"] == "query GreetingQuery"
        assert query_span["data"]["graphql.operation.type"] == "query"
        assert query_span["data"]["graphql.operation.name"] == "GreetingQuery"
        assert query_span["data"]["graphql.resource_name"]

        if send_default_pii is True:
            assert query_span["data"]["graphql.document"] == query
        else:
            assert "graphql.document" not in query_span["data"]

        parse_spans = [
            span
            for span in transaction_event["spans"]
            if span["op"] == OP.GRAPHQL_PARSE
        ]
        assert len(parse_spans) == 1, "exactly one parse span expected"
        parse_span = parse_spans[0]
        assert parse_span["parent_span_id"] == query_span["span_id"]
        assert parse_span["description"] == "parsing"

        validate_spans = [
            span
            for span in transaction_event["spans"]
            if span["op"] == OP.GRAPHQL_VALIDATE
        ]
        assert len(validate_spans) == 1, "exactly one validate span expected"
        validate_span = validate_spans[0]
        assert validate_span["parent_span_id"] == query_span["span_id"]
        assert validate_span["description"] == "validation"

        resolve_spans = [
            span
            for span in transaction_event["spans"]
            if span["op"] == OP.GRAPHQL_RESOLVE
        ]
        assert len(resolve_spans) == 1, "exactly one resolve span expected"
        resolve_span = resolve_spans[0]
        assert resolve_span["parent_span_id"] == query_span["span_id"]
        assert resolve_span["description"] == "resolving Query.hello"
        assert resolve_span["data"] == ApproxDict(
            {
                "graphql.field_name": "hello",
                "graphql.parent_type": "Query",
                "graphql.field_path": "Query.hello",
                "graphql.path": "hello",
            }
        )


@parameterize_strawberry_test
@pytest.mark.parametrize(
    "send_default_pii",
    [True, False],
)
@pytest.mark.parametrize("span_streaming", [True, False])
def test_transaction_no_operation_name(
    request,
    sentry_init,
    capture_events,
    capture_items,
    client_factory,
    async_execution,
    framework_integrations,
    send_default_pii,
    span_streaming,
):
    sentry_init(
        integrations=[
            StrawberryIntegration(async_execution=async_execution),
        ]
        + framework_integrations,
        traces_sample_rate=1,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming:
        items = capture_items("span")
    else:
        events = capture_events()

    schema = strawberry.Schema(Query)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    query = "{ hello }"
    client.post("/graphql", json={"query": query})

    if span_streaming:
        sentry_sdk.flush()
        spans = [i.payload for i in items]

        assert len(spans) == 5
        parse_span, validate_span, resolve_span, query_span, segment = spans

        assert segment["is_segment"] is True
        if async_execution:
            assert segment["name"] == "/graphql"
        else:
            assert segment["name"] == "graphql_view"

        assert query_span["attributes"]["sentry.op"] == OP.GRAPHQL_QUERY
        assert query_span["name"] == "query"
        assert query_span["attributes"]["graphql.operation.type"] == "query"
        assert "graphql.operation.name" not in query_span["attributes"]

        if send_default_pii is True:
            assert query_span["attributes"]["graphql.document"] == query
        else:
            assert "graphql.document" not in query_span["attributes"]

        assert parse_span["attributes"]["sentry.op"] == OP.GRAPHQL_PARSE
        assert parse_span["name"] == "parsing"
        assert parse_span["parent_span_id"] == query_span["span_id"]

        assert validate_span["attributes"]["sentry.op"] == OP.GRAPHQL_VALIDATE
        assert validate_span["name"] == "validation"
        assert validate_span["parent_span_id"] == query_span["span_id"]

        assert resolve_span["attributes"]["sentry.op"] == OP.GRAPHQL_RESOLVE
        assert resolve_span["name"] == "resolving Query.hello"
        assert resolve_span["parent_span_id"] == query_span["span_id"]
    else:
        assert len(events) == 1
        (transaction_event,) = events

        if async_execution:
            assert transaction_event["transaction"] == "/graphql"
        else:
            assert transaction_event["transaction"] == "graphql_view"

        assert transaction_event["spans"]

        query_spans = [
            span
            for span in transaction_event["spans"]
            if span["op"] == OP.GRAPHQL_QUERY
        ]
        assert len(query_spans) == 1, "exactly one query span expected"
        query_span = query_spans[0]
        assert query_span["description"] == "query"
        assert query_span["data"]["graphql.operation.type"] == "query"
        assert query_span["data"]["graphql.operation.name"] is None
        assert query_span["data"]["graphql.resource_name"]

        if send_default_pii is True:
            assert query_span["data"]["graphql.document"] == query
        else:
            assert "graphql.document" not in query_span["data"]

        parse_spans = [
            span
            for span in transaction_event["spans"]
            if span["op"] == OP.GRAPHQL_PARSE
        ]
        assert len(parse_spans) == 1, "exactly one parse span expected"
        parse_span = parse_spans[0]
        assert parse_span["parent_span_id"] == query_span["span_id"]
        assert parse_span["description"] == "parsing"

        validate_spans = [
            span
            for span in transaction_event["spans"]
            if span["op"] == OP.GRAPHQL_VALIDATE
        ]
        assert len(validate_spans) == 1, "exactly one validate span expected"
        validate_span = validate_spans[0]
        assert validate_span["parent_span_id"] == query_span["span_id"]
        assert validate_span["description"] == "validation"

        resolve_spans = [
            span
            for span in transaction_event["spans"]
            if span["op"] == OP.GRAPHQL_RESOLVE
        ]
        assert len(resolve_spans) == 1, "exactly one resolve span expected"
        resolve_span = resolve_spans[0]
        assert resolve_span["parent_span_id"] == query_span["span_id"]
        assert resolve_span["description"] == "resolving Query.hello"
        assert resolve_span["data"] == ApproxDict(
            {
                "graphql.field_name": "hello",
                "graphql.parent_type": "Query",
                "graphql.field_path": "Query.hello",
                "graphql.path": "hello",
            }
        )


@parameterize_strawberry_test
@pytest.mark.parametrize(
    "send_default_pii",
    [True, False],
)
@pytest.mark.parametrize("span_streaming", [True, False])
def test_transaction_mutation(
    request,
    sentry_init,
    capture_events,
    capture_items,
    client_factory,
    async_execution,
    framework_integrations,
    send_default_pii,
    span_streaming,
):
    sentry_init(
        integrations=[
            StrawberryIntegration(async_execution=async_execution),
        ]
        + framework_integrations,
        traces_sample_rate=1,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming:
        items = capture_items("span")
    else:
        events = capture_events()

    schema = strawberry.Schema(Query, mutation=Mutation)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    query = 'mutation Change { change(attribute: "something") }'
    client.post("/graphql", json={"query": query})

    if span_streaming:
        sentry_sdk.flush()
        spans = [i.payload for i in items]

        assert len(spans) == 5
        parse_span, validate_span, resolve_span, mutation_span, segment = spans

        assert segment["is_segment"] is True
        assert segment["name"] == "Change"
        assert segment["attributes"]["sentry.op"] == OP.GRAPHQL_MUTATION

        assert mutation_span["attributes"]["sentry.op"] == OP.GRAPHQL_MUTATION
        assert mutation_span["name"] == "mutation"
        assert mutation_span["attributes"]["graphql.operation.type"] == "mutation"
        assert "graphql.operation.name" not in mutation_span["attributes"]

        if send_default_pii is True:
            assert mutation_span["attributes"]["graphql.document"] == query
        else:
            assert "graphql.document" not in mutation_span["attributes"]

        assert parse_span["attributes"]["sentry.op"] == OP.GRAPHQL_PARSE
        assert parse_span["name"] == "parsing"
        assert parse_span["parent_span_id"] == mutation_span["span_id"]

        assert validate_span["attributes"]["sentry.op"] == OP.GRAPHQL_VALIDATE
        assert validate_span["name"] == "validation"
        assert validate_span["parent_span_id"] == mutation_span["span_id"]

        assert resolve_span["attributes"]["sentry.op"] == OP.GRAPHQL_RESOLVE
        assert resolve_span["name"] == "resolving Mutation.change"
        assert resolve_span["parent_span_id"] == mutation_span["span_id"]
    else:
        assert len(events) == 1
        (transaction_event,) = events

        assert transaction_event["transaction"] == "Change"
        assert transaction_event["contexts"]["trace"]["op"] == OP.GRAPHQL_MUTATION
        assert transaction_event["spans"]

        query_spans = [
            span
            for span in transaction_event["spans"]
            if span["op"] == OP.GRAPHQL_MUTATION
        ]
        assert len(query_spans) == 1, "exactly one mutation span expected"
        query_span = query_spans[0]
        assert query_span["description"] == "mutation"
        assert query_span["data"]["graphql.operation.type"] == "mutation"
        assert query_span["data"]["graphql.operation.name"] is None
        assert query_span["data"]["graphql.resource_name"]

        if send_default_pii is True:
            assert query_span["data"]["graphql.document"] == query
        else:
            assert "graphql.document" not in query_span["data"]

        parse_spans = [
            span
            for span in transaction_event["spans"]
            if span["op"] == OP.GRAPHQL_PARSE
        ]
        assert len(parse_spans) == 1, "exactly one parse span expected"
        parse_span = parse_spans[0]
        assert parse_span["parent_span_id"] == query_span["span_id"]
        assert parse_span["description"] == "parsing"

        validate_spans = [
            span
            for span in transaction_event["spans"]
            if span["op"] == OP.GRAPHQL_VALIDATE
        ]
        assert len(validate_spans) == 1, "exactly one validate span expected"
        validate_span = validate_spans[0]
        assert validate_span["parent_span_id"] == query_span["span_id"]
        assert validate_span["description"] == "validation"

        resolve_spans = [
            span
            for span in transaction_event["spans"]
            if span["op"] == OP.GRAPHQL_RESOLVE
        ]
        assert len(resolve_spans) == 1, "exactly one resolve span expected"
        resolve_span = resolve_spans[0]
        assert resolve_span["parent_span_id"] == query_span["span_id"]
        assert resolve_span["description"] == "resolving Mutation.change"
        assert resolve_span["data"] == ApproxDict(
            {
                "graphql.field_name": "change",
                "graphql.parent_type": "Mutation",
                "graphql.field_path": "Mutation.change",
                "graphql.path": "change",
            }
        )


@parameterize_strawberry_test
@pytest.mark.parametrize(
    "data_collection,send_default_pii,expect_document",
    [
        pytest.param(
            {"graphql": {"document": True}},
            None,
            True,
            id="document_on_sets_graphql_document",
        ),
        pytest.param(
            {"graphql": {"document": False}},
            None,
            False,
            id="document_off_omits_graphql_document",
        ),
        pytest.param(
            {"graphql": {"document": False}},
            True,
            False,
            id="data_collection_takes_precedence_over_send_default_pii_on",
        ),
        pytest.param(
            {"graphql": {"document": True}},
            False,
            True,
            id="data_collection_takes_precedence_over_send_default_pii_off",
        ),
    ],
)
@pytest.mark.parametrize("span_streaming", [True, False])
def test_graphql_span_data_collection(
    request,
    sentry_init,
    capture_events,
    capture_items,
    client_factory,
    async_execution,
    framework_integrations,
    data_collection,
    send_default_pii,
    expect_document,
    span_streaming,
):
    init_kwargs = {
        "integrations": [StrawberryIntegration(async_execution=async_execution)]
        + framework_integrations,
        "traces_sample_rate": 1,
        "trace_lifecycle": "stream" if span_streaming else "static",
        "_experiments": {"data_collection": data_collection},
    }
    if send_default_pii is not None:
        init_kwargs["send_default_pii"] = send_default_pii
    sentry_init(**init_kwargs)

    if span_streaming:
        items = capture_items("span")
    else:
        events = capture_events()

    schema = strawberry.Schema(Query)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    query = "query GreetingQuery { hello }"
    client.post("/graphql", json={"query": query, "operationName": "GreetingQuery"})

    if span_streaming:
        sentry_sdk.flush()
        spans = [i.payload for i in items]
        assert len(spans) == 5
        query_span = spans[3]

        assert query_span["attributes"]["sentry.op"] == OP.GRAPHQL_QUERY
        assert query_span["attributes"]["graphql.operation.type"] == "query"
        # operation.name is always set when an operation name is present
        assert query_span["attributes"]["graphql.operation.name"] == "GreetingQuery"

        if expect_document:
            assert query_span["attributes"]["graphql.document"] == query
        else:
            assert "graphql.document" not in query_span["attributes"]
    else:
        assert len(events) == 1
        (transaction_event,) = events

        query_spans = [
            span
            for span in transaction_event["spans"]
            if span["op"] == OP.GRAPHQL_QUERY
        ]
        assert len(query_spans) == 1, "exactly one query span expected"
        query_span = query_spans[0]
        assert query_span["data"]["graphql.operation.type"] == "query"
        assert query_span["data"]["graphql.operation.name"] == "GreetingQuery"

        if expect_document:
            assert query_span["data"]["graphql.document"] == query
        else:
            assert "graphql.document" not in query_span["data"]


@parameterize_strawberry_test
def test_handle_none_query_gracefully(
    request,
    sentry_init,
    capture_events,
    client_factory,
    async_execution,
    framework_integrations,
):
    sentry_init(
        integrations=[
            StrawberryIntegration(async_execution=async_execution),
        ]
        + framework_integrations,
    )
    events = capture_events()

    schema = strawberry.Schema(Query)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    client.post("/graphql", json={})

    assert len(events) == 0, "expected no events to be sent to Sentry"


@parameterize_strawberry_test
def test_handle_none_query_gracefully_with_data_collection(
    request,
    sentry_init,
    capture_events,
    client_factory,
    async_execution,
    framework_integrations,
):
    sentry_init(
        integrations=[
            StrawberryIntegration(async_execution=async_execution),
        ]
        + framework_integrations,
        _experiments={
            "data_collection": {"graphql": {"document": True, "variables": True}}
        },
    )
    events = capture_events()

    schema = strawberry.Schema(Query)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    client.post("/graphql", json={})

    assert len(events) == 0, "expected no events to be sent to Sentry"


@parameterize_strawberry_test
@pytest.mark.parametrize("span_streaming", [True, False])
def test_span_origin(
    request,
    sentry_init,
    capture_events,
    capture_items,
    client_factory,
    async_execution,
    framework_integrations,
    span_streaming,
):
    """
    Tests for OP.GRAPHQL_MUTATION, OP.GRAPHQL_PARSE, OP.GRAPHQL_VALIDATE, OP.GRAPHQL_RESOLVE,
    """
    sentry_init(
        integrations=[
            StrawberryIntegration(async_execution=async_execution),
        ]
        + framework_integrations,
        traces_sample_rate=1,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming:
        items = capture_items("span")
    else:
        events = capture_events()

    schema = strawberry.Schema(Query, mutation=Mutation)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    query = 'mutation Change { change(attribute: "something") }'
    client.post("/graphql", json={"query": query})

    is_flask = "Flask" in str(framework_integrations[0])

    if span_streaming:
        sentry_sdk.flush()
        spans = [i.payload for i in items]

        assert len(spans) == 5
        parse_span, validate_span, resolve_span, mutation_span, segment = spans

        assert segment["is_segment"] is True
        if is_flask:
            assert segment["attributes"]["sentry.origin"] == "auto.http.flask"
        else:
            assert segment["attributes"]["sentry.origin"] == "auto.http.starlette"

        for span in (parse_span, validate_span, resolve_span, mutation_span):
            assert span["attributes"]["sentry.origin"] == "auto.graphql.strawberry"
    else:
        (event,) = events

        if is_flask:
            assert event["contexts"]["trace"]["origin"] == "auto.http.flask"
        else:
            assert event["contexts"]["trace"]["origin"] == "auto.http.starlette"

        for span in event["spans"]:
            if span["op"].startswith("graphql."):
                assert span["origin"] == "auto.graphql.strawberry"


@parameterize_strawberry_test
@pytest.mark.parametrize("span_streaming", [True, False])
def test_span_origin2(
    request,
    sentry_init,
    capture_events,
    capture_items,
    client_factory,
    async_execution,
    framework_integrations,
    span_streaming,
):
    """
    Tests for OP.GRAPHQL_QUERY
    """
    sentry_init(
        integrations=[
            StrawberryIntegration(async_execution=async_execution),
        ]
        + framework_integrations,
        traces_sample_rate=1,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming:
        items = capture_items("span")
    else:
        events = capture_events()

    schema = strawberry.Schema(Query, mutation=Mutation)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    query = "query GreetingQuery { hello }"
    client.post("/graphql", json={"query": query, "operationName": "GreetingQuery"})

    is_flask = "Flask" in str(framework_integrations[0])

    if span_streaming:
        sentry_sdk.flush()
        spans = [i.payload for i in items]

        assert len(spans) == 5
        parse_span, validate_span, resolve_span, query_span, segment = spans

        assert segment["is_segment"] is True
        if is_flask:
            assert segment["attributes"]["sentry.origin"] == "auto.http.flask"
        else:
            assert segment["attributes"]["sentry.origin"] == "auto.http.starlette"

        for span in (parse_span, validate_span, resolve_span, query_span):
            assert span["attributes"]["sentry.origin"] == "auto.graphql.strawberry"
    else:
        (event,) = events

        if is_flask:
            assert event["contexts"]["trace"]["origin"] == "auto.http.flask"
        else:
            assert event["contexts"]["trace"]["origin"] == "auto.http.starlette"

        for span in event["spans"]:
            if span["op"].startswith("graphql."):
                assert span["origin"] == "auto.graphql.strawberry"


@parameterize_strawberry_test
@pytest.mark.parametrize("span_streaming", [True, False])
def test_span_origin3(
    request,
    sentry_init,
    capture_events,
    capture_items,
    client_factory,
    async_execution,
    framework_integrations,
    span_streaming,
):
    """
    Tests for OP.GRAPHQL_SUBSCRIPTION
    """
    sentry_init(
        integrations=[
            StrawberryIntegration(async_execution=async_execution),
        ]
        + framework_integrations,
        traces_sample_rate=1,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming:
        items = capture_items("span")
    else:
        events = capture_events()

    schema = strawberry.Schema(Query, subscription=Subscription)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    query = "subscription { messageAdded { content } }"
    client.post("/graphql", json={"query": query})

    is_flask = "Flask" in str(framework_integrations[0])

    if span_streaming:
        sentry_sdk.flush()
        spans = [i.payload for i in items]

        assert len(spans) == 5
        parse_span, validate_span, resolve_span, subscription_span, segment = spans

        assert segment["is_segment"] is True
        if is_flask:
            assert segment["attributes"]["sentry.origin"] == "auto.http.flask"
        else:
            assert segment["attributes"]["sentry.origin"] == "auto.http.starlette"

        for span in (parse_span, validate_span, resolve_span, subscription_span):
            assert span["attributes"]["sentry.origin"] == "auto.graphql.strawberry"
    else:
        (event,) = events

        if is_flask:
            assert event["contexts"]["trace"]["origin"] == "auto.http.flask"
        else:
            assert event["contexts"]["trace"]["origin"] == "auto.http.starlette"

        for span in event["spans"]:
            if span["op"].startswith("graphql."):
                assert span["origin"] == "auto.graphql.strawberry"
