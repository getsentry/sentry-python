import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from flask import Flask, jsonify, request
from graphene import ObjectType, Schema, String

import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.flask import FlaskIntegration
from sentry_sdk.integrations.graphene import GrapheneIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

DATA_COLLECTION_GRAPHQL_DOCUMENTS_PARAMS = [
    pytest.param(
        {"graphql": {"document": True}},
        None,
        True,
        id="document_on_collects_graphql_data",
    ),
    pytest.param(
        {"graphql": {"document": False}},
        None,
        False,
        id="document_off_omits_graphql_data",
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
]


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


@pytest.mark.parametrize(
    "data_collection,send_default_pii,expect_api_target",
    DATA_COLLECTION_GRAPHQL_DOCUMENTS_PARAMS,
)
def test_event_processor_data_collection_sync(
    sentry_init, capture_events, data_collection, send_default_pii, expect_api_target
):
    init_kwargs = {
        "integrations": [GrapheneIntegration(), FlaskIntegration()],
        "_experiments": {"data_collection": data_collection},
    }
    if send_default_pii is not None:
        init_kwargs["send_default_pii"] = send_default_pii
    sentry_init(**init_kwargs)
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
    if expect_api_target:
        assert event["request"]["api_target"] == "graphql"
        assert "data" in event.get("request", {})
    else:
        assert "api_target" not in event.get("request", {})
        assert "data" not in event.get("request", {})


@pytest.mark.parametrize(
    "data_collection,send_default_pii,expect_api_target",
    DATA_COLLECTION_GRAPHQL_DOCUMENTS_PARAMS,
)
def test_event_processor_data_collection_async(
    sentry_init, capture_events, data_collection, send_default_pii, expect_api_target
):
    init_kwargs = {
        "integrations": [
            GrapheneIntegration(),
            FastApiIntegration(),
            StarletteIntegration(),
        ],
        "_experiments": {"data_collection": data_collection},
    }

    if send_default_pii is not None:
        init_kwargs["send_default_pii"] = send_default_pii
    sentry_init(**init_kwargs)

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

    if expect_api_target:
        assert event["request"]["api_target"] == "graphql"
        assert "data" in event.get("request", {})
    else:
        assert "api_target" not in event.get("request", {})
        assert "data" not in event.get("request", {})


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


@pytest.mark.parametrize(
    "send_default_pii",
    [True, False],
)
def test_graphql_span_holds_query_information(
    sentry_init, capture_items, send_default_pii
):
    sentry_init(
        integrations=[GrapheneIntegration(), FlaskIntegration()],
        traces_sample_rate=1.0,
        default_integrations=False,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )
    items = capture_items("span")

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

    sentry_sdk.get_client().flush()

    spans = [item.payload for item in items]
    assert len(spans) == 2

    graphql_span, flask_segment = spans

    assert graphql_span["name"] == query["operationName"]
    assert graphql_span["attributes"]["sentry.op"] == OP.GRAPHQL_QUERY
    assert (
        graphql_span["attributes"]["graphql.operation.name"] == query["operationName"]
    )
    assert graphql_span["attributes"]["graphql.operation.type"] == "query"
    assert graphql_span["is_segment"] is False

    if send_default_pii is True:
        assert graphql_span["attributes"]["graphql.document"] == query["query"]
    else:
        assert "graphql.document" not in graphql_span["attributes"]

    assert flask_segment["is_segment"] is True
    assert graphql_span["parent_span_id"] == flask_segment["span_id"]


@pytest.mark.parametrize(
    "data_collection,send_default_pii,expect_document",
    DATA_COLLECTION_GRAPHQL_DOCUMENTS_PARAMS,
)
def test_graphql_span_data_collection(
    sentry_init, capture_items, data_collection, send_default_pii, expect_document
):
    init_kwargs = {
        "integrations": [GrapheneIntegration(), FlaskIntegration()],
        "traces_sample_rate": 1.0,
        "default_integrations": False,
        "trace_lifecycle": "stream",
        "_experiments": {"data_collection": data_collection},
    }
    if send_default_pii is not None:
        init_kwargs["send_default_pii"] = send_default_pii
    sentry_init(**init_kwargs)
    items = capture_items("span")

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

    sentry_sdk.get_client().flush()

    spans = [item.payload for item in items]
    assert len(spans) == 2

    graphql_span, flask_segment = spans

    assert graphql_span["name"] == query["operationName"]
    assert graphql_span["attributes"]["sentry.op"] == OP.GRAPHQL_QUERY
    assert (
        graphql_span["attributes"]["graphql.operation.name"] == query["operationName"]
    )
    assert graphql_span["attributes"]["graphql.operation.type"] == "query"
    assert graphql_span["is_segment"] is False

    if expect_document:
        assert graphql_span["attributes"]["graphql.document"] == query["query"]
    else:
        assert "graphql.document" not in graphql_span["attributes"]

    assert flask_segment["is_segment"] is True
    assert graphql_span["parent_span_id"] == flask_segment["span_id"]


def test_breadcrumbs_hold_query_information_on_error(
    sentry_init, capture_items
):
    sentry_init(
        integrations=[
            GrapheneIntegration(),
        ],
        default_integrations=False,
        trace_lifecycle="stream",
    )
    items = capture_items("span", "event")

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

    sentry_sdk.get_client().flush()

    events = [item.payload for item in items if item.type == "event"]
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
