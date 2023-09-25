import pytest

strawberry = pytest.importorskip("strawberry")
pytest.importorskip("fastapi")
pytest.importorskip("flask")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from flask import Flask
from strawberry.fastapi import GraphQLRouter
from strawberry.flask.views import GraphQLView

from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.flask import FlaskIntegration
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.integrations.strawberry import StrawberryIntegration

ignore_logger("strawberry*")


@strawberry.type
class Query:
    @strawberry.field
    def hello(self) -> str:
        return "Hello World"

    @strawberry.field
    def error(self) -> str:
        raise RuntimeError("oh no!")


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
    )
    events = capture_events()

    schema = strawberry.Schema(Query)

    async_app = FastAPI()
    async_app.include_router(GraphQLRouter(schema), prefix="/graphql")

    query = {"query": "query ErrorQuery { error }"}
    client = TestClient(async_app)
    client.post("/graphql", json=query)

    assert len(events) == 1

    (error_event,) = events
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "strawberry"
    assert error_event["request"]["api_target"] == "graphql"
    assert error_event["request"]["data"] == query


def test_capture_request_if_available_and_send_pii_is_on_sync(
    sentry_init, capture_events
):
    sentry_init(
        send_default_pii=True,
        integrations=[StrawberryIntegration(), FlaskIntegration()],
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

    assert len(events) == 1

    (error_event,) = events
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "strawberry"
    assert error_event["request"]["api_target"] == "graphql"
    assert error_event["request"]["data"] == query


def test_do_not_capture_request_if_send_pii_is_off_async(sentry_init, capture_events):
    sentry_init(
        integrations=[
            StrawberryIntegration(),
            FastApiIntegration(),
            StarletteIntegration(),
        ],
    )
    events = capture_events()

    schema = strawberry.Schema(Query)

    async_app = FastAPI()
    async_app.include_router(GraphQLRouter(schema), prefix="/graphql")

    query = {"query": "query ErrorQuery { error }"}
    client = TestClient(async_app)
    client.post("/graphql", json=query)

    assert len(events) == 1

    (error_event,) = events
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "strawberry"
    assert "data" not in error_event["request"]
    assert "response" not in error_event["contexts"]


def test_do_not_capture_request_if_send_pii_is_off_sync(sentry_init, capture_events):
    sentry_init(
        integrations=[StrawberryIntegration(), FlaskIntegration()],
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

    assert len(events) == 1

    (error_event,) = events
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "strawberry"
    assert "data" not in error_event["request"]
    assert "response" not in error_event["contexts"]


def test_no_event_if_no_errors_async(sentry_init, capture_events):
    sentry_init(
        integrations=[
            StrawberryIntegration(),
            FastApiIntegration(),
            StarletteIntegration(),
        ],
    )
    events = capture_events()

    schema = strawberry.Schema(Query)

    async_app = FastAPI()
    async_app.include_router(GraphQLRouter(schema), prefix="/graphql")

    query = {"query": "query GreetingQuery { hello }"}
    client = TestClient(async_app)
    client.post("/graphql", json=query)

    assert len(events) == 0


def test_no_event_if_no_errors_sync(sentry_init, capture_events):
    sentry_init(
        integrations=[
            StrawberryIntegration(),
            FlaskIntegration(),
        ],
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

    assert len(events) == 0
