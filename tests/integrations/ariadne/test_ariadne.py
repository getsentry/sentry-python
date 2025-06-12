from ariadne import gql, graphql_sync, ObjectType, QueryType, make_executable_schema
from ariadne.asgi import GraphQL
from fastapi import FastAPI
from fastapi.testclient import TestClient
from flask import Flask, request, jsonify

from sentry_sdk.integrations.ariadne import AriadneIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.flask import FlaskIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration


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
    def resolve_greeting(*_, **kwargs):
        name = kwargs.pop("name")
        return {"name": name}

    @query.field("error")
    def resolve_error(obj, *_):
        raise RuntimeError("resolver failed")

    @greeting.field("name")
    def resolve_name(obj, *_):
        return "Hello, {}!".format(obj["name"])

    return make_executable_schema(type_defs, query)


def test_capture_request_and_response_if_send_pii_is_on_async(
    sentry_init, capture_events
):
    sentry_init(
        send_default_pii=True,
        integrations=[
            AriadneIntegration(),
            FastApiIntegration(),
            StarletteIntegration(),
        ],
    )
    events = capture_events()

    schema = schema_factory()

    async_app = FastAPI()
    async_app.mount("/graphql/", GraphQL(schema))

    query = {"query": "query ErrorQuery {error}"}
    client = TestClient(async_app)
    client.post("/graphql", json=query)

    assert len(events) == 1

    (event,) = events
    assert len(event["exception"]["values"]) == 2
    assert event["exception"]["values"][0]["mechanism"]["type"] == "chained"
    assert event["exception"]["values"][-1]["mechanism"]["type"] == "ariadne"
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


def test_capture_request_and_response_if_send_pii_is_on_sync(
    sentry_init, capture_events
):
    sentry_init(
        send_default_pii=True,
        integrations=[AriadneIntegration(), FlaskIntegration()],
    )
    events = capture_events()

    schema = schema_factory()

    sync_app = Flask(__name__)

    @sync_app.route("/graphql", methods=["POST"])
    def graphql_server():
        data = request.get_json()
        success, result = graphql_sync(schema, data)
        return jsonify(result), 200

    query = {"query": "query ErrorQuery {error}"}
    client = sync_app.test_client()
    client.post("/graphql", json=query)

    assert len(events) == 1

    (event,) = events
    assert len(event["exception"]["values"]) == 2
    assert event["exception"]["values"][0]["mechanism"]["type"] == "chained"
    assert event["exception"]["values"][-1]["mechanism"]["type"] == "ariadne"

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


def test_do_not_capture_request_and_response_if_send_pii_is_off_async(
    sentry_init, capture_events
):
    sentry_init(
        integrations=[
            AriadneIntegration(),
            FastApiIntegration(),
            StarletteIntegration(),
        ],
    )
    events = capture_events()

    schema = schema_factory()

    async_app = FastAPI()
    async_app.mount("/graphql/", GraphQL(schema))

    query = {"query": "query ErrorQuery {error}"}
    client = TestClient(async_app)
    client.post("/graphql", json=query)

    assert len(events) == 1

    (event,) = events
    assert len(event["exception"]["values"]) == 2
    assert event["exception"]["values"][0]["mechanism"]["type"] == "chained"
    assert event["exception"]["values"][-1]["mechanism"]["type"] == "ariadne"

    assert "data" not in event["request"]
    assert "response" not in event["contexts"]


def test_do_not_capture_request_and_response_if_send_pii_is_off_sync(
    sentry_init, capture_events
):
    sentry_init(
        integrations=[AriadneIntegration(), FlaskIntegration()],
    )
    events = capture_events()

    schema = schema_factory()

    sync_app = Flask(__name__)

    @sync_app.route("/graphql", methods=["POST"])
    def graphql_server():
        data = request.get_json()
        success, result = graphql_sync(schema, data)
        return jsonify(result), 200

    query = {"query": "query ErrorQuery {error}"}
    client = sync_app.test_client()
    client.post("/graphql", json=query)

    assert len(events) == 1

    (event,) = events
    assert len(event["exception"]["values"]) == 2
    assert event["exception"]["values"][0]["mechanism"]["type"] == "chained"
    assert event["exception"]["values"][-1]["mechanism"]["type"] == "ariadne"
    assert "data" not in event["request"]
    assert "response" not in event["contexts"]


def test_capture_validation_error(sentry_init, capture_events):
    sentry_init(
        send_default_pii=True,
        integrations=[
            AriadneIntegration(),
            FastApiIntegration(),
            StarletteIntegration(),
        ],
    )
    events = capture_events()

    schema = schema_factory()

    async_app = FastAPI()
    async_app.mount("/graphql/", GraphQL(schema))

    query = {"query": "query ErrorQuery {doesnt_exist}"}
    client = TestClient(async_app)
    client.post("/graphql", json=query)

    assert len(events) == 1

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "ariadne"
    assert event["contexts"]["response"] == {
        "data": {
            "errors": [
                {
                    "locations": [{"column": 19, "line": 1}],
                    "message": "Cannot query field 'doesnt_exist' on type 'Query'.",
                }
            ]
        }
    }
    assert event["request"]["api_target"] == "graphql"
    assert event["request"]["data"] == query


def test_no_event_if_no_errors_async(sentry_init, capture_events):
    sentry_init(
        integrations=[
            AriadneIntegration(),
            FastApiIntegration(),
            StarletteIntegration(),
        ],
    )
    events = capture_events()

    schema = schema_factory()

    async_app = FastAPI()
    async_app.mount("/graphql/", GraphQL(schema))

    query = {
        "query": "query GreetingQuery($name: String) { greeting(name: $name) {name} }",
        "variables": {"name": "some name"},
    }
    client = TestClient(async_app)
    client.post("/graphql", json=query)

    assert len(events) == 0


def test_no_event_if_no_errors_sync(sentry_init, capture_events):
    sentry_init(
        integrations=[AriadneIntegration(), FlaskIntegration()],
    )
    events = capture_events()

    schema = schema_factory()

    sync_app = Flask(__name__)

    @sync_app.route("/graphql", methods=["POST"])
    def graphql_server():
        data = request.get_json()
        success, result = graphql_sync(schema, data)
        return jsonify(result), 200

    query = {
        "query": "query GreetingQuery($name: String) { greeting(name: $name) {name} }",
        "variables": {"name": "some name"},
    }
    client = sync_app.test_client()
    client.post("/graphql", json=query)

    assert len(events) == 0
