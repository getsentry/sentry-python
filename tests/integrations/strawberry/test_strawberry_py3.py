import pytest

strawberry = pytest.importorskip("strawberry")
pytest.importorskip("fastapi")
pytest.importorskip("flask")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from flask import Flask
from strawberry.extensions.tracing import (  # XXX conditional on strawberry version
    SentryTracingExtension,
    SentryTracingExtensionSync,
)
from strawberry.fastapi import GraphQLRouter
from strawberry.flask.views import GraphQLView

from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.flask import FlaskIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.integrations.strawberry import (
    StrawberryIntegration,
    SentryAsyncExtension,
    SentrySyncExtension,
)

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


@strawberry.type
class Query:
    @strawberry.field
    def hello(self) -> str:
        return "Hello World"

    @strawberry.field
    def error(self) -> str:
        return 1 / 0


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


def test_replace_existing_sentry_async_extension(sentry_init):
    sentry_init(integrations=[StrawberryIntegration()])

    schema = strawberry.Schema(Query, extensions=[SentryTracingExtension])
    assert SentryTracingExtension not in schema.extensions
    assert SentryAsyncExtension in schema.extensions


def test_replace_existing_sentry_sync_extension(sentry_init):
    sentry_init(integrations=[StrawberryIntegration()])

    schema = strawberry.Schema(Query, extensions=[SentryTracingExtensionSync])
    assert SentryTracingExtensionSync not in schema.extensions
    assert SentrySyncExtension in schema.extensions


def test_capture_request_if_available_and_send_pii_is_on_async(
    sentry_init, capture_events
):
    sentry_init(
        send_default_pii=True,
        integrations=[
            StrawberryIntegration(),
            FastApiIntegration(),
            StarletteIntegration(),
        ],
        traces_sample_rate=1,
    )
    events = capture_events()

    schema = strawberry.Schema(Query)

    async_app = FastAPI()
    async_app.include_router(GraphQLRouter(schema), prefix="/graphql")

    query = {"query": "query ErrorQuery { error }"}
    client = TestClient(async_app)
    client.post("/graphql", json=query)

    assert len(events) == 2

    (error_event, transaction_event) = events
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "strawberry"
    assert error_event["request"]["api_target"] == "graphql"
    assert error_event["request"]["data"] == query
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


def test_capture_request_if_available_and_send_pii_is_on_sync(
    sentry_init, capture_events
):
    sentry_init(
        send_default_pii=True,
        integrations=[StrawberryIntegration(async_execution=False), FlaskIntegration()],
        traces_sample_rate=1,
    )
    events = capture_events()

    schema = strawberry.Schema(Query)

    sync_app = Flask(__name__)
    sync_app.add_url_rule(
        "/graphql",
        view_func=GraphQLView.as_view("graphql_view", schema=schema),
    )

    query = {"query": "query ErrorQuery { error }"}
    client = sync_app.test_client()
    client.post("/graphql", json=query)

    assert len(events) == 2

    (error_event, transaction_event) = events
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "strawberry"
    assert error_event["request"]["api_target"] == "graphql"
    assert error_event["request"]["data"] == query
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


def test_do_not_capture_request_if_send_pii_is_off_async(sentry_init, capture_events):
    sentry_init(
        integrations=[
            StrawberryIntegration(),
            FastApiIntegration(),
            StarletteIntegration(),
        ],
        traces_sample_rate=1,
    )
    events = capture_events()

    schema = strawberry.Schema(Query)

    async_app = FastAPI()
    async_app.include_router(GraphQLRouter(schema), prefix="/graphql")

    query = {"query": "query ErrorQuery { error }"}
    client = TestClient(async_app)
    client.post("/graphql", json=query)

    assert len(events) == 2

    (error_event, transaction_event) = events
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "strawberry"
    assert "data" not in error_event["request"]
    assert "response" not in error_event["contexts"]


def test_do_not_capture_request_if_send_pii_is_off_sync(sentry_init, capture_events):
    sentry_init(
        integrations=[StrawberryIntegration(async_execution=False), FlaskIntegration()],
        traces_sample_rate=1,
    )
    events = capture_events()

    schema = strawberry.Schema(Query)

    sync_app = Flask(__name__)
    sync_app.add_url_rule(
        "/graphql",
        view_func=GraphQLView.as_view("graphql_view", schema=schema),
    )

    query = {"query": "query ErrorQuery { error }"}
    client = sync_app.test_client()
    client.post("/graphql", json=query)

    assert len(events) == 2

    (error_event, transaction_event) = events
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "strawberry"
    assert "data" not in error_event["request"]
    assert "response" not in error_event["contexts"]


def test_no_errors_async(sentry_init, capture_events):
    sentry_init(
        integrations=[
            StrawberryIntegration(),
            FastApiIntegration(),
            StarletteIntegration(),
        ],
        traces_sample_rate=1,
    )
    events = capture_events()

    schema = strawberry.Schema(Query)

    async_app = FastAPI()
    async_app.include_router(GraphQLRouter(schema), prefix="/graphql")

    query = {"query": "query GreetingQuery { hello }"}
    client = TestClient(async_app)
    client.post("/graphql", json=query)

    assert len(events) == 1
    assert False


def test_no_errors_sync(sentry_init, capture_events):
    sentry_init(
        integrations=[
            StrawberryIntegration(async_execution=False),
            FlaskIntegration(),
        ],
        traces_sample_rate=1,
    )
    events = capture_events()

    schema = strawberry.Schema(Query)

    sync_app = Flask(__name__)
    sync_app.add_url_rule(
        "/graphql",
        view_func=GraphQLView.as_view("graphql_view", schema=schema),
    )

    query = {
        "query": "query GreetingQuery { hello }",
    }
    client = sync_app.test_client()
    client.post("/graphql", json=query)

    assert len(events) == 1
    assert False
