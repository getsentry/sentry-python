import pytest

import responses
from gql import gql
from gql import Client
from gql.transport.exceptions import TransportQueryError
from gql.transport.requests import RequestsHTTPTransport
from sentry_sdk.integrations.gql import GQLIntegration


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


def _make_erroneous_query(capture_events):
    """
    Make an erroneous GraphQL query, and assert that the error was reraised, that
    exactly one event was recorded, and that the exception recorded was a
    TransportQueryError. Then, return the event to allow further verifications.
    """
    events = capture_events()
    response_json = {"errors": ["something bad happened"]}

    with pytest.raises(TransportQueryError):
        _execute_mock_query(response_json)

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


def test_real_gql_request_no_error(sentry_init, capture_events):
    """
    Integration test verifying that the GQLIntegration works as expected with successful query.
    """
    sentry_init(integrations=[GQLIntegration()])
    events = capture_events()

    response_data = {"example": "This is the example"}
    response_json = {"data": response_data}

    result = _execute_mock_query(response_json)

    assert result == response_data, (
        "client.execute returned a different value from what it received from the server"
    )
    assert len(events) == 0, (
        "the sdk captured an event, even though the query was successful"
    )


def test_real_gql_request_with_error_no_pii(sentry_init, capture_events):
    """
    Integration test verifying that the GQLIntegration works as expected with query resulting
    in a GraphQL error, and that PII is not sent.
    """
    sentry_init(integrations=[GQLIntegration()])

    event = _make_erroneous_query(capture_events)

    assert "data" not in event["request"]
    assert "response" not in event["contexts"]


def test_real_gql_request_with_error_with_pii(sentry_init, capture_events):
    """
    Integration test verifying that the GQLIntegration works as expected with query resulting
    in a GraphQL error, and that PII is not sent.
    """
    sentry_init(integrations=[GQLIntegration()], send_default_pii=True)

    event = _make_erroneous_query(capture_events)

    assert "data" in event["request"]
    assert "response" in event["contexts"]
