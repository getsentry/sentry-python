import pytest
from ariadne import ObjectType, QueryType, gql, graphql_sync, make_executable_schema
from ariadne.asgi import GraphQL
from fastapi import FastAPI
from fastapi.testclient import TestClient
from flask import Flask, jsonify, request
from flask import request as flask_request

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
    assert event["exception"]["values"][0]["mechanism"]["type"] == "ariadne"
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
    assert event["exception"]["values"][0]["mechanism"]["type"] == "ariadne"
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


ERROR_QUERY_WITH_VARIABLES = {
    "query": (
        "query GreetingQuery($name: String) { greeting(name: $name) {name} error }"
    ),
    "variables": {"name": "some name"},
}


@pytest.fixture(params=["flask", "fastapi"])
def graphql_client(request):
    """Build a test client for each supported framework, hitting an endpoint
    whose resolver raises so an event is always captured."""

    def make_client():
        schema = schema_factory()
        if request.param == "flask":
            app = Flask(__name__)

            @app.route("/graphql", methods=["POST"])
            def graphql_server():
                success, result = graphql_sync(schema, flask_request.get_json())
                return jsonify(result), 200

            return app.test_client()

        async_app = FastAPI()
        async_app.mount("/graphql/", GraphQL(schema))
        return TestClient(async_app)

    return make_client


def _init_all_integrations(sentry_init, **kwargs):
    sentry_init(
        integrations=[
            AriadneIntegration(),
            FlaskIntegration(),
            FastApiIntegration(),
            StarletteIntegration(),
        ],
        **kwargs,
    )


@pytest.mark.parametrize(
    "init_kwargs,expect_query,expect_variables",
    [
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            True,
            True,
            id="data_collection_defaults",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "graphql": {"document": True, "variables": True}
                    }
                }
            },
            True,
            True,
            id="document_on_variables_on",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {"graphql": {"document": False}}}},
            False,
            True,
            id="document_off_variables_on",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {"graphql": {"variables": False}}}},
            True,
            False,
            id="document_on_variables_off",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "graphql": {"document": False, "variables": False}
                    }
                }
            },
            None,
            None,
            id="document_off_variables_off",
        ),
        pytest.param(
            {
                "send_default_pii": True,
                "_experiments": {"data_collection": {"graphql": {"document": False}}},
            },
            False,
            True,
            id="data_collection_takes_precedence_over_send_default_pii_on",
        ),
        pytest.param(
            {
                "send_default_pii": False,
                "_experiments": {"data_collection": {"graphql": {"document": True}}},
            },
            True,
            True,
            id="data_collection_takes_precedence_over_send_default_pii_off",
        ),
    ],
)
def test_request_data_collection(
    sentry_init,
    capture_events,
    graphql_client,
    init_kwargs,
    expect_query,
    expect_variables,
):
    """
    Verify that the ``data_collection`` ``graphql.document`` and
    ``graphql.variables`` toggles independently filter the request data
    attached to error events.
    """
    _init_all_integrations(sentry_init, **init_kwargs)
    events = capture_events()

    graphql_client().post("/graphql", json=ERROR_QUERY_WITH_VARIABLES)

    assert len(events) == 1
    (event,) = events

    if expect_query is None:
        assert "data" not in event["request"]
        return

    assert event["request"]["api_target"] == "graphql"
    assert ("query" in event["request"]["data"]) == expect_query
    assert ("variables" in event["request"]["data"]) == expect_variables

    # Response body capture is intentionally tied to send_default_pii only.
    assert ("response" in event["contexts"]) == bool(
        init_kwargs.get("send_default_pii")
    )


def test_request_data_collection_body_out_of_bounds_still_collects_variables(
    sentry_init, capture_events, graphql_client
):
    """
    When the request body exceeds ``max_request_body_size``, the document is
    dropped but variables (which are not subject to the bounds check) are
    still collected.
    """
    _init_all_integrations(
        sentry_init,
        max_request_body_size="small",
        _experiments={"data_collection": {}},
    )
    events = capture_events()

    query = dict(ERROR_QUERY_WITH_VARIABLES)
    # The integration reads Content-Length from the payload's "headers" key;
    # report a size over the "small" limit (10**3).
    query["headers"] = {"Content-Length": str(10**4)}
    graphql_client().post("/graphql", json=query)

    assert len(events) == 1
    (event,) = events

    assert "query" not in event["request"]["data"]
    assert event["request"]["data"]["variables"] == {"name": "some name"}
