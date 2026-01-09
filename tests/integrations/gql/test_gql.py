import pytest

import responses
from gql import gql
from gql import Client
from gql import __version__
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
