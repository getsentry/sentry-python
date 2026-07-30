import pytest
import responses
from gql import Client, __version__, gql
from gql.transport.exceptions import TransportQueryError
from gql.transport.requests import RequestsHTTPTransport

from sentry_sdk.integrations.gql import GQLIntegration
from sentry_sdk.utils import parse_version

GQL_VERSION = parse_version(__version__)


@responses.activate
def _execute_mock_query(response_json):
    url = "http://example.com/graphql"
    query_string = """
        query Example {
            example
        }
    """

    # Mock the GraphQL server response
    responses.add(
        method=responses.POST,
        url=url,
        json=response_json,
        status=200,
    )

    transport = RequestsHTTPTransport(url=url)
    client = Client(transport=transport)
    query = gql(query_string)

    return client.execute(query)


@responses.activate
def _execute_mock_query_with_keyword_document(response_json):
    url = "http://example.com/graphql"
    query_string = """
        query Example {
            example
        }
    """

    # Mock the GraphQL server response
    responses.add(
        method=responses.POST,
        url=url,
        json=response_json,
        status=200,
    )

    transport = RequestsHTTPTransport(url=url)
    client = Client(transport=transport)
    query = gql(query_string)

    return client.execute(document=query)


@responses.activate
def _execute_mock_query_with_variables(response_json):
    url = "http://example.com/graphql"
    query_string = """
        query Example($id: ID!) {
            example(id: $id)
        }
    """

    # Mock the GraphQL server response
    responses.add(
        method=responses.POST,
        url=url,
        json=response_json,
        status=200,
    )

    transport = RequestsHTTPTransport(url=url)
    client = Client(transport=transport)
    query = gql(query_string)

    return client.execute(query, variable_values={"id": "1"})


_execute_query_funcs = [_execute_mock_query]
if GQL_VERSION < (4,):
    _execute_query_funcs.append(_execute_mock_query_with_keyword_document)


def _make_erroneous_query(capture_events, execute_query):
    """
    Make an erroneous GraphQL query, and assert that the error was reraised, that
    exactly one event was recorded, and that the exception recorded was a
    TransportQueryError. Then, return the event to allow further verifications.
    """
    events = capture_events()
    response_json = {"errors": ["something bad happened"]}

    with pytest.raises(TransportQueryError):
        execute_query(response_json)

    assert len(events) == 1, (
        "the sdk captured %d events, but 1 event was expected" % len(events)
    )

    (event,) = events
    (exception,) = event["exception"]["values"]

    assert exception["type"] == "TransportQueryError", (
        "%s was captured, but we expected a TransportQueryError" % exception(type)
    )

    assert "request" in event

    return event


def test_gql_init(sentry_init):
    """
    Integration test to ensure we can initialize the SDK with the GQL Integration
    """
    sentry_init(integrations=[GQLIntegration()])


@pytest.mark.parametrize("execute_query", _execute_query_funcs)
def test_real_gql_request_no_error(sentry_init, capture_events, execute_query):
    """
    Integration test verifying that the GQLIntegration works as expected with successful query.
    """
    sentry_init(integrations=[GQLIntegration()])
    events = capture_events()

    response_data = {"example": "This is the example"}
    response_json = {"data": response_data}

    result = execute_query(response_json)

    assert result == response_data, (
        "client.execute returned a different value from what it received from the server"
    )
    assert len(events) == 0, (
        "the sdk captured an event, even though the query was successful"
    )


@pytest.mark.parametrize("execute_query", _execute_query_funcs)
def test_real_gql_request_with_error_no_pii(sentry_init, capture_events, execute_query):
    """
    Integration test verifying that the GQLIntegration works as expected with query resulting
    in a GraphQL error, and that PII is not sent.
    """
    sentry_init(integrations=[GQLIntegration()])

    event = _make_erroneous_query(capture_events, execute_query)

    assert "data" not in event["request"]
    assert "response" not in event["contexts"]


@pytest.mark.parametrize("execute_query", _execute_query_funcs)
def test_real_gql_request_with_error_with_pii(
    sentry_init, capture_events, execute_query
):
    """
    Integration test verifying that the GQLIntegration works as expected with query resulting
    in a GraphQL error, and that PII is not sent.
    """
    sentry_init(integrations=[GQLIntegration()], send_default_pii=True)

    event = _make_erroneous_query(capture_events, execute_query)

    assert "data" in event["request"]
    assert "response" in event["contexts"]


@pytest.mark.parametrize("execute_query", _execute_query_funcs)
@pytest.mark.parametrize(
    "init_kwargs,expect_data",
    [
        pytest.param({}, False, id="no_pii_no_data_collection"),
        pytest.param({"send_default_pii": True}, True, id="legacy_pii_on"),
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            True,
            id="data_collection_defaults",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {"graphql": {"document": True}}}},
            True,
            id="data_collection_document_on",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {"graphql": {"document": False}}}},
            False,
            id="data_collection_document_off",
        ),
        pytest.param(
            {
                "send_default_pii": True,
                "_experiments": {"data_collection": {"graphql": {"document": False}}},
            },
            False,
            id="data_collection_takes_precedence_over_send_default_pii_on",
        ),
        pytest.param(
            {
                "send_default_pii": False,
                "_experiments": {"data_collection": {"graphql": {"document": True}}},
            },
            True,
            id="data_collection_takes_precedence_over_send_default_pii_off",
        ),
    ],
)
def test_real_gql_request_with_error_data_collection(
    sentry_init, capture_events, execute_query, init_kwargs, expect_data
):
    """
    Integration test verifying that the GQLIntegration honours the
    ``data_collection`` configuration when deciding whether to attach the
    GraphQL document to the event.
    """
    sentry_init(integrations=[GQLIntegration()], **init_kwargs)

    event = _make_erroneous_query(capture_events, execute_query)

    if expect_data:
        assert "data" in event["request"]
        assert "query" in event["request"]["data"]
        assert "response" in event["contexts"]
    else:
        assert "data" not in event["request"]
        assert "response" not in event["contexts"]


@pytest.mark.parametrize(
    "init_kwargs,expect_variables",
    [
        pytest.param({"send_default_pii": True}, True, id="legacy_pii_on"),
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            True,
            id="data_collection_defaults",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {"graphql": {"variables": True}}}},
            True,
            id="data_collection_variables_on",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {"graphql": {"variables": False}}}},
            False,
            id="data_collection_variables_off",
        ),
    ],
)
def test_real_gql_request_with_error_data_collection_variables(
    sentry_init, capture_events, init_kwargs, expect_variables
):
    """
    Integration test verifying that the GQLIntegration honours the
    ``data_collection`` ``graphql.variables`` toggle for queries that
    define variables.
    """
    sentry_init(integrations=[GQLIntegration()], **init_kwargs)

    event = _make_erroneous_query(capture_events, _execute_mock_query_with_variables)

    assert "data" in event["request"]
    assert "query" in event["request"]["data"]

    if expect_variables:
        assert event["request"]["data"].get("variables")
    else:
        assert not event["request"]["data"].get("variables")
