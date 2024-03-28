from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from flask import Flask, request, jsonify
from graphene import ObjectType, String, Schema

from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.flask import FlaskIntegration
from sentry_sdk.integrations.graphene import GrapheneIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration


class Query(ObjectType):
    hello = String(first_name=String(default_value="stranger"))
    goodbye = String()

    def resolve_hello(root, info, first_name):  # noqa: N805
        return "Hello {}!".format(first_name)

    def resolve_goodbye(root, info):  # noqa: N805
        raise RuntimeError("oh no!")


def test_capture_request_if_available_and_send_pii_is_on_async(
    sentry_init, capture_events
):
    sentry_init(
        send_default_pii=True,
        integrations=[
            GrapheneIntegration(),
            FastApiIntegration(),
            StarletteIntegration(),
        ],
    )
    events = capture_events()

    schema = Schema(query=Query)

    async_app = FastAPI()

    @async_app.post("/graphql")
    async def graphql_server_async(request: Request):
        data = await request.json()
        result = await schema.execute_async(data["query"])
        return result.data

    query = {"query": "query ErrorQuery {goodbye}"}
    client = TestClient(async_app)
    client.post("/graphql", json=query)

    assert len(events) == 1

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "graphene"
    assert event["request"]["api_target"] == "graphql"
    assert event["request"]["data"] == query


def test_capture_request_if_available_and_send_pii_is_on_sync(
    sentry_init, capture_events
):
    sentry_init(
        send_default_pii=True,
        integrations=[GrapheneIntegration(), FlaskIntegration()],
    )
    events = capture_events()

    schema = Schema(query=Query)

    sync_app = Flask(__name__)

    @sync_app.route("/graphql", methods=["POST"])
    def graphql_server_sync():
        data = request.get_json()
        result = schema.execute(data["query"])
        return jsonify(result.data), 200

    query = {"query": "query ErrorQuery {goodbye}"}
    client = sync_app.test_client()
    client.post("/graphql", json=query)

    assert len(events) == 1

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "graphene"
    assert event["request"]["api_target"] == "graphql"
    assert event["request"]["data"] == query


def test_do_not_capture_request_if_send_pii_is_off_async(sentry_init, capture_events):
    sentry_init(
        integrations=[
            GrapheneIntegration(),
            FastApiIntegration(),
            StarletteIntegration(),
        ],
    )
    events = capture_events()

    schema = Schema(query=Query)

    async_app = FastAPI()

    @async_app.post("/graphql")
    async def graphql_server_async(request: Request):
        data = await request.json()
        result = await schema.execute_async(data["query"])
        return result.data

    query = {"query": "query ErrorQuery {goodbye}"}
    client = TestClient(async_app)
    client.post("/graphql", json=query)

    assert len(events) == 1

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "graphene"
    assert "data" not in event["request"]
    assert "response" not in event["contexts"]


def test_do_not_capture_request_if_send_pii_is_off_sync(sentry_init, capture_events):
    sentry_init(
        integrations=[GrapheneIntegration(), FlaskIntegration()],
    )
    events = capture_events()

    schema = Schema(query=Query)

    sync_app = Flask(__name__)

    @sync_app.route("/graphql", methods=["POST"])
    def graphql_server_sync():
        data = request.get_json()
        result = schema.execute(data["query"])
        return jsonify(result.data), 200

    query = {"query": "query ErrorQuery {goodbye}"}
    client = sync_app.test_client()
    client.post("/graphql", json=query)

    assert len(events) == 1

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "graphene"
    assert "data" not in event["request"]
    assert "response" not in event["contexts"]


def test_no_event_if_no_errors_async(sentry_init, capture_events):
    sentry_init(
        integrations=[
            GrapheneIntegration(),
            FastApiIntegration(),
            StarletteIntegration(),
        ],
    )
    events = capture_events()

    schema = Schema(query=Query)

    async_app = FastAPI()

    @async_app.post("/graphql")
    async def graphql_server_async(request: Request):
        data = await request.json()
        result = await schema.execute_async(data["query"])
        return result.data

    query = {
        "query": "query GreetingQuery { hello }",
    }
    client = TestClient(async_app)
    client.post("/graphql", json=query)

    assert len(events) == 0


def test_no_event_if_no_errors_sync(sentry_init, capture_events):
    sentry_init(
        integrations=[
            GrapheneIntegration(),
            FlaskIntegration(),
        ],
    )
    events = capture_events()

    schema = Schema(query=Query)

    sync_app = Flask(__name__)

    @sync_app.route("/graphql", methods=["POST"])
    def graphql_server_sync():
        data = request.get_json()
        result = schema.execute(data["query"])
        return jsonify(result.data), 200

    query = {
        "query": "query GreetingQuery { hello }",
    }
    client = sync_app.test_client()
    client.post("/graphql", json=query)

    assert len(events) == 0


def test_transaction_name_sync(sentry_init, capture_events):
    sentry_init(
        integrations=[GrapheneIntegration()],
        enable_tracing=True,
        auto_enabling_integrations=False,
    )
    events = capture_events()

    schema = Schema(query=Query)

    sync_app = Flask(__name__)

    @sync_app.post("/graphql")
    def graphql_server_sync():
        data = request.get_json()
        result = schema.execute(data["query"], operation_name=data.get("operationName"))
        return result.data

    query = {"query": "query GreetingQuery { hello }", "operationName": "GreetingQuery"}
    client = sync_app.test_client()
    client.post("/graphql", json=query)

    assert len(events) == 1

    (transaction_event,) = events
    assert transaction_event["transaction"] == "GreetingQuery"
    assert transaction_event["type"] == "transaction"
