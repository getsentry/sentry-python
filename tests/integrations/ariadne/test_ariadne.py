import pytest

pytest.importorskip("ariadne")
pytest.importorskip("fastapi")
pytest.importorskip("flask")

from ariadne import gql, graphql_sync, ObjectType, QueryType, make_executable_schema
from ariadne.asgi import GraphQL
from fastapi import FastAPI
from fastapi.testclient import TestClient
from flask import Flask, request, jsonify

from sentry_sdk.integrations.ariadne import AriadneIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.flask import FlaskIntegration
from sentry_sdk.integrations.logging import ignore_logger


# to prevent the logging integration from capturing the exception first
ignore_logger("ariadne")


def schema_factory():
    type_defs = gql(
        """
        type Query {
            greeting(name: String): Greeting
            error: String
        }

        type Greeting {
            name: String
        }
    """
    )

    query = QueryType()
    greeting = ObjectType("Greeting")

    @query.field("greeting")
    def resolve_greeting(*_, name=None):
        return {"name": name}

    @query.field("error")
    def resolve_error(obj, *_):
        raise RuntimeError("resolver failed")

    @greeting.field("name")
    def resolve_name(obj, *_):
        return "Hello, {}!".format(obj["name"])

    return make_executable_schema(type_defs, query)


schema = schema_factory()

async_app = FastAPI()
async_app.mount("/graphql/", GraphQL(schema))

sync_app = Flask(__name__)


@sync_app.route("/graphql", methods=["POST"])
def graphql_server():
    data = request.get_json()
    success, result = graphql_sync(schema, data)
    return jsonify(result), 200


@pytest.mark.parametrize(
    "client",
    [TestClient(async_app), sync_app.test_client()],
)
def test_capture_request_and_response_if_send_pii_is_on(
    sentry_init, capture_events, client
):
    sentry_init(
        send_default_pii=True,
        integrations=[AriadneIntegration(), FastApiIntegration(), FlaskIntegration()],
    )
    events = capture_events()

    query = {"query": "query ErrorQuery {error}"}
    client.post("/graphql", json=query)

    assert len(events) == 1

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "ariadne"
    assert event["contexts"]["response"] == {
        "data": {
            "data": {"error": None},
            "errors": [
                {
                    "locations": [{"column": 19, "line": 1}],
                    "message": "resolver failed",
                    "path": ["error"],
                }
            ],
        }
    }
    assert event["request"]["api_target"] == "graphql"
    assert event["request"]["data"] == query


@pytest.mark.parametrize(
    "client",
    [TestClient(async_app), sync_app.test_client()],
)
def test_do_not_capture_request_and_response_if_send_pii_is_off(
    sentry_init, capture_events, client
):
    sentry_init(
        integrations=[AriadneIntegration(), FastApiIntegration(), FlaskIntegration()],
    )
    events = capture_events()

    query = {"query": "query ErrorQuery {error}"}
    client.post("/graphql", json=query)

    assert len(events) == 1

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "ariadne"
    assert "data" not in event["request"]
    assert "response" not in event["contexts"]


@pytest.mark.parametrize(
    "client",
    [TestClient(async_app), sync_app.test_client()],
)
def test_no_event_if_no_errors(sentry_init, capture_events, client):
    sentry_init(
        integrations=[AriadneIntegration(), FastApiIntegration(), FlaskIntegration()],
    )
    events = capture_events()

    query = {
        "query": "query GreetingQuery($name: String) { greeting(name: $name) {name} }",
        "variables": {"name": "some name"},
    }
    client.post("/graphql", json=query)

    assert len(events) == 0
