import pytest

from ariadne import ObjectType, QueryType, gql, make_executable_schema
from ariadne.asgi import GraphQL
from ariadne.graphql import graphql as graphql_async, graphql_sync
from fastapi import FastAPI
from fastapi.testclient import TestClient
from flask import Flask, request, jsonify

from sentry_sdk.integrations.ariadne import AriadneIntegration
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
        return f"Hello, {obj['name']}!"

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
        integrations=[AriadneIntegration()],
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
    from pprint import pprint

    pprint(event["request"])
    assert event["request"]["api_target"] == "graphql"
    assert event["request"]["data"] == query


@pytest.mark.parametrize(
    "graphql_run",
    [graphql_async, graphql_sync],
)
def test_nothing_captured_on_success(sentry_init, capture_events, graphql_run):
    sentry_init(
        send_default_pii=True,
        integrations=[AriadneIntegration()],
    )
    events = capture_events()

    schema = schema_factory()

    graphql_run(
        schema,
        data={
            "query": "query GreetingQuery($name: String) { greeting(name: $name) {name}}",
            "variables": {"name": "some name"},
        },
    )

    assert len(events) == 0
