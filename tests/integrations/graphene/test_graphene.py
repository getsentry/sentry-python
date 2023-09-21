import pytest

pytest.importorskip("graphene")
pytest.importorskip("fastapi")
pytest.importorskip("flask")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from flask import Flask, request, jsonify
from graphene import ObjectType, String, Schema

from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.flask import FlaskIntegration
from sentry_sdk.integrations.graphene import GrapheneIntegration


class Query(ObjectType):
    hello = String(first_name=String(default_value="stranger"))
    goodbye = String()

    def resolve_hello(root, info, first_name):
        return "Hello {}!".format(first_name)

    def resolve_goodbye(root, info):
        raise RuntimeError("oh no!")


schema = Schema(query=Query)

async_app = FastAPI()


@async_app.post("/graphql")
async def graphql_server_async(request):
    data = await request.json()
    result = schema.execute(data["query"])
    return result.data


sync_app = Flask(__name__)


@sync_app.route("/graphql", methods=["POST"])
def graphql_server_sync():
    data = request.get_json()
    result = schema.execute(data["query"])
    return jsonify(result.data), 200


@pytest.mark.parametrize(
    "client",
    [TestClient(async_app), sync_app.test_client()],
)
def test_capture_request_if_send_pii_is_on(sentry_init, capture_events, client):
    sentry_init(
        send_default_pii=True,
        integrations=[GrapheneIntegration(), FastApiIntegration(), FlaskIntegration()],
    )
    events = capture_events()

    query = {"query": "query ErrorQuery {goodbye}"}
    client.post("/graphql", json=query)

    assert len(events) == 1

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "graphene"
    assert event["request"]["api_target"] == "graphql"
    assert event["request"]["data"] == query


@pytest.mark.parametrize(
    "client",
    [TestClient(async_app), sync_app.test_client()],
)
def test_do_not_capture_request_if_send_pii_is_off(sentry_init, capture_events, client):
    sentry_init(
        integrations=[GrapheneIntegration(), FastApiIntegration(), FlaskIntegration()],
    )
    events = capture_events()

    query = {"query": "query ErrorQuery {goodbye}"}
    client.post("/graphql", json=query)

    assert len(events) == 1

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "graphene"
    assert "data" not in event["request"]
    assert "response" not in event["contexts"]


@pytest.mark.parametrize(
    "client",
    [TestClient(async_app), sync_app.test_client()],
)
def test_no_event_if_no_errors(sentry_init, capture_events, client):
    sentry_init(
        integrations=[GrapheneIntegration(), FastApiIntegration(), FlaskIntegration()],
    )
    events = capture_events()

    query = {
        "query": "query GreetingQuery { hello }",
    }
    client.post("/graphql", json=query)

    assert len(events) == 0
