import pytest
from typing import AsyncGenerator, Optional

strawberry = pytest.importorskip("strawberry")
pytest.importorskip("fastapi")
pytest.importorskip("flask")

from unittest import mock

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
    StrawberryIntegration,
    SentryAsyncExtension,
    SentrySyncExtension,
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

    with mock.patch(
        "sentry_sdk.integrations.strawberry._get_installed_modules",
        return_value={"flask": "2.3.3"},
    ):
        # actual installed modules should not matter, the explicit option takes
        # precedence
        schema = strawberry.Schema(Query)
        assert SentryAsyncExtension in schema.extensions


def test_sync_execution_uses_sync_extension(sentry_init):
    sentry_init(integrations=[StrawberryIntegration(async_execution=False)])

    with mock.patch(
        "sentry_sdk.integrations.strawberry._get_installed_modules",
        return_value={"fastapi": "0.103.1", "starlette": "0.27.0"},
    ):
        # actual installed modules should not matter, the explicit option takes
        # precedence
        schema = strawberry.Schema(Query)
        assert SentrySyncExtension in schema.extensions


def test_infer_execution_type_from_installed_packages_async(sentry_init):
    sentry_init(integrations=[StrawberryIntegration()])

    with mock.patch(
        "sentry_sdk.integrations.strawberry._get_installed_modules",
        return_value={"fastapi": "0.103.1", "starlette": "0.27.0"},
    ):
        schema = strawberry.Schema(Query)
        assert SentryAsyncExtension in schema.extensions


def test_infer_execution_type_from_installed_packages_sync(sentry_init):
    sentry_init(integrations=[StrawberryIntegration()])

    with mock.patch(
        "sentry_sdk.integrations.strawberry._get_installed_modules",
        return_value={"flask": "2.3.3"},
    ):
        schema = strawberry.Schema(Query)
        assert SentrySyncExtension in schema.extensions


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
def test_capture_transaction_on_error(
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
        traces_sample_rate=1,
    )
    events = capture_events()

    schema = strawberry.Schema(Query)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    query = "query ErrorQuery { error }"
    client.post("/graphql", json={"query": query, "operationName": "ErrorQuery"})

    assert len(events) == 2
    (_, transaction_event) = events

    assert transaction_event["transaction"] == "ErrorQuery"
    assert transaction_event["contexts"]["trace"]["op"] == OP.GRAPHQL_QUERY
    assert transaction_event["spans"]

    query_spans = [
        span for span in transaction_event["spans"] if span["op"] == OP.GRAPHQL_QUERY
    ]
    assert len(query_spans) == 1, "exactly one query span expected"
    query_span = query_spans[0]
    assert query_span["description"] == "query ErrorQuery"
    assert query_span["data"]["graphql.operation.type"] == "query"
    assert query_span["data"]["graphql.operation.name"] == "ErrorQuery"
    assert query_span["data"]["graphql.document"] == query
    assert query_span["data"]["graphql.resource_name"]

    parse_spans = [
        span for span in transaction_event["spans"] if span["op"] == OP.GRAPHQL_PARSE
    ]
    assert len(parse_spans) == 1, "exactly one parse span expected"
    parse_span = parse_spans[0]
    assert parse_span["parent_span_id"] == query_span["span_id"]
    assert parse_span["description"] == "parsing"

    validate_spans = [
        span for span in transaction_event["spans"] if span["op"] == OP.GRAPHQL_VALIDATE
    ]
    assert len(validate_spans) == 1, "exactly one validate span expected"
    validate_span = validate_spans[0]
    assert validate_span["parent_span_id"] == query_span["span_id"]
    assert validate_span["description"] == "validation"

    resolve_spans = [
        span for span in transaction_event["spans"] if span["op"] == OP.GRAPHQL_RESOLVE
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
def test_capture_transaction_on_success(
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
        traces_sample_rate=1,
    )
    events = capture_events()

    schema = strawberry.Schema(Query)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    query = "query GreetingQuery { hello }"
    client.post("/graphql", json={"query": query, "operationName": "GreetingQuery"})

    assert len(events) == 1
    (transaction_event,) = events

    assert transaction_event["transaction"] == "GreetingQuery"
    assert transaction_event["contexts"]["trace"]["op"] == OP.GRAPHQL_QUERY
    assert transaction_event["spans"]

    query_spans = [
        span for span in transaction_event["spans"] if span["op"] == OP.GRAPHQL_QUERY
    ]
    assert len(query_spans) == 1, "exactly one query span expected"
    query_span = query_spans[0]
    assert query_span["description"] == "query GreetingQuery"
    assert query_span["data"]["graphql.operation.type"] == "query"
    assert query_span["data"]["graphql.operation.name"] == "GreetingQuery"
    assert query_span["data"]["graphql.document"] == query
    assert query_span["data"]["graphql.resource_name"]

    parse_spans = [
        span for span in transaction_event["spans"] if span["op"] == OP.GRAPHQL_PARSE
    ]
    assert len(parse_spans) == 1, "exactly one parse span expected"
    parse_span = parse_spans[0]
    assert parse_span["parent_span_id"] == query_span["span_id"]
    assert parse_span["description"] == "parsing"

    validate_spans = [
        span for span in transaction_event["spans"] if span["op"] == OP.GRAPHQL_VALIDATE
    ]
    assert len(validate_spans) == 1, "exactly one validate span expected"
    validate_span = validate_spans[0]
    assert validate_span["parent_span_id"] == query_span["span_id"]
    assert validate_span["description"] == "validation"

    resolve_spans = [
        span for span in transaction_event["spans"] if span["op"] == OP.GRAPHQL_RESOLVE
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
def test_transaction_no_operation_name(
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
        traces_sample_rate=1,
    )
    events = capture_events()

    schema = strawberry.Schema(Query)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    query = "{ hello }"
    client.post("/graphql", json={"query": query})

    assert len(events) == 1
    (transaction_event,) = events

    if async_execution:
        assert transaction_event["transaction"] == "/graphql"
    else:
        assert transaction_event["transaction"] == "graphql_view"

    assert transaction_event["spans"]

    query_spans = [
        span for span in transaction_event["spans"] if span["op"] == OP.GRAPHQL_QUERY
    ]
    assert len(query_spans) == 1, "exactly one query span expected"
    query_span = query_spans[0]
    assert query_span["description"] == "query"
    assert query_span["data"]["graphql.operation.type"] == "query"
    assert query_span["data"]["graphql.operation.name"] is None
    assert query_span["data"]["graphql.document"] == query
    assert query_span["data"]["graphql.resource_name"]

    parse_spans = [
        span for span in transaction_event["spans"] if span["op"] == OP.GRAPHQL_PARSE
    ]
    assert len(parse_spans) == 1, "exactly one parse span expected"
    parse_span = parse_spans[0]
    assert parse_span["parent_span_id"] == query_span["span_id"]
    assert parse_span["description"] == "parsing"

    validate_spans = [
        span for span in transaction_event["spans"] if span["op"] == OP.GRAPHQL_VALIDATE
    ]
    assert len(validate_spans) == 1, "exactly one validate span expected"
    validate_span = validate_spans[0]
    assert validate_span["parent_span_id"] == query_span["span_id"]
    assert validate_span["description"] == "validation"

    resolve_spans = [
        span for span in transaction_event["spans"] if span["op"] == OP.GRAPHQL_RESOLVE
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
def test_transaction_mutation(
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
        traces_sample_rate=1,
    )
    events = capture_events()

    schema = strawberry.Schema(Query, mutation=Mutation)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    query = 'mutation Change { change(attribute: "something") }'
    client.post("/graphql", json={"query": query})

    assert len(events) == 1
    (transaction_event,) = events

    assert transaction_event["transaction"] == "Change"
    assert transaction_event["contexts"]["trace"]["op"] == OP.GRAPHQL_MUTATION
    assert transaction_event["spans"]

    query_spans = [
        span for span in transaction_event["spans"] if span["op"] == OP.GRAPHQL_MUTATION
    ]
    assert len(query_spans) == 1, "exactly one mutation span expected"
    query_span = query_spans[0]
    assert query_span["description"] == "mutation"
    assert query_span["data"]["graphql.operation.type"] == "mutation"
    assert query_span["data"]["graphql.operation.name"] is None
    assert query_span["data"]["graphql.document"] == query
    assert query_span["data"]["graphql.resource_name"]

    parse_spans = [
        span for span in transaction_event["spans"] if span["op"] == OP.GRAPHQL_PARSE
    ]
    assert len(parse_spans) == 1, "exactly one parse span expected"
    parse_span = parse_spans[0]
    assert parse_span["parent_span_id"] == query_span["span_id"]
    assert parse_span["description"] == "parsing"

    validate_spans = [
        span for span in transaction_event["spans"] if span["op"] == OP.GRAPHQL_VALIDATE
    ]
    assert len(validate_spans) == 1, "exactly one validate span expected"
    validate_span = validate_spans[0]
    assert validate_span["parent_span_id"] == query_span["span_id"]
    assert validate_span["description"] == "validation"

    resolve_spans = [
        span for span in transaction_event["spans"] if span["op"] == OP.GRAPHQL_RESOLVE
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
def test_span_origin(
    request,
    sentry_init,
    capture_events,
    client_factory,
    async_execution,
    framework_integrations,
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
    )
    events = capture_events()

    schema = strawberry.Schema(Query, mutation=Mutation)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    query = 'mutation Change { change(attribute: "something") }'
    client.post("/graphql", json={"query": query})

    (event,) = events

    is_flask = "Flask" in str(framework_integrations[0])
    if is_flask:
        assert event["contexts"]["trace"]["origin"] == "auto.http.flask"
    else:
        assert event["contexts"]["trace"]["origin"] == "auto.http.starlette"

    for span in event["spans"]:
        if span["op"].startswith("graphql."):
            assert span["origin"] == "auto.graphql.strawberry"


@parameterize_strawberry_test
def test_span_origin2(
    request,
    sentry_init,
    capture_events,
    client_factory,
    async_execution,
    framework_integrations,
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
    )
    events = capture_events()

    schema = strawberry.Schema(Query, mutation=Mutation)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    query = "query GreetingQuery { hello }"
    client.post("/graphql", json={"query": query, "operationName": "GreetingQuery"})

    (event,) = events

    is_flask = "Flask" in str(framework_integrations[0])
    if is_flask:
        assert event["contexts"]["trace"]["origin"] == "auto.http.flask"
    else:
        assert event["contexts"]["trace"]["origin"] == "auto.http.starlette"

    for span in event["spans"]:
        if span["op"].startswith("graphql."):
            assert span["origin"] == "auto.graphql.strawberry"


@parameterize_strawberry_test
def test_span_origin3(
    request,
    sentry_init,
    capture_events,
    client_factory,
    async_execution,
    framework_integrations,
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
    )
    events = capture_events()

    schema = strawberry.Schema(Query, subscription=Subscription)

    client_factory = request.getfixturevalue(client_factory)
    client = client_factory(schema)

    query = "subscription { messageAdded { content } }"
    client.post("/graphql", json={"query": query})

    (event,) = events

    is_flask = "Flask" in str(framework_integrations[0])
    if is_flask:
        assert event["contexts"]["trace"]["origin"] == "auto.http.flask"
    else:
        assert event["contexts"]["trace"]["origin"] == "auto.http.starlette"

    for span in event["spans"]:
        if span["op"].startswith("graphql."):
            assert span["origin"] == "auto.graphql.strawberry"
