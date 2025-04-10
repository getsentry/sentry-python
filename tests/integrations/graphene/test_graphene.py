from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from flask import Flask, request, jsonify
from graphene import ObjectType, String, Schema

from sentry_sdk.consts import OP
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


def test_graphql_span_holds_query_information(sentry_init, capture_events):
    sentry_init(
        integrations=[GrapheneIntegration(), FlaskIntegration()],
        traces_sample_rate=1.0,
        default_integrations=False,
    )
    events = capture_events()

    schema = Schema(query=Query)

    sync_app = Flask(__name__)

    @sync_app.route("/graphql", methods=["POST"])
    def graphql_server_sync():
        data = request.get_json()
        result = schema.execute(data["query"], operation_name=data.get("operationName"))
        return jsonify(result.data), 200

    query = {
        "query": "query GreetingQuery { hello }",
        "operationName": "GreetingQuery",
    }
    client = sync_app.test_client()
    client.post("/graphql", json=query)

    assert len(events) == 1

    (event,) = events
    assert len(event["spans"]) == 1

    (span,) = event["spans"]
    assert span["op"] == OP.GRAPHQL_QUERY
    assert span["description"] == query["operationName"]
    assert span["data"]["graphql.document"] == query["query"]
    assert span["data"]["graphql.operation.name"] == query["operationName"]
    assert span["data"]["graphql.operation.type"] == "query"


def test_breadcrumbs_hold_query_information_on_error(sentry_init, capture_events):
    sentry_init(
        integrations=[
            GrapheneIntegration(),
        ],
        default_integrations=False,
    )
    events = capture_events()

    schema = Schema(query=Query)

    sync_app = Flask(__name__)

    @sync_app.route("/graphql", methods=["POST"])
    def graphql_server_sync():
        data = request.get_json()
        result = schema.execute(data["query"], operation_name=data.get("operationName"))
        return jsonify(result.data), 200

    query = {
        "query": "query ErrorQuery { goodbye }",
        "operationName": "ErrorQuery",
    }
    client = sync_app.test_client()
    client.post("/graphql", json=query)

    assert len(events) == 1

    (event,) = events
    assert len(event["breadcrumbs"]) == 1

    breadcrumbs = event["breadcrumbs"]["values"]
    assert len(breadcrumbs) == 1

    (breadcrumb,) = breadcrumbs
    assert breadcrumb["category"] == "graphql.operation"
    assert breadcrumb["data"]["operation_name"] == query["operationName"]
    assert breadcrumb["data"]["operation_type"] == "query"
    assert breadcrumb["type"] == "default"
