import pytest

pytest.importorskip("gql")

import responses
from gql import gql
from gql import Client
from gql.transport.exceptions import TransportQueryError
from gql.transport.requests import RequestsHTTPTransport
from graphql import DocumentNode
from sentry_sdk.integrations.gql import GQLIntegration
from unittest.mock import MagicMock, patch


class _MockClientBase(MagicMock):
    """
    Mocked version of GQL Client class, following same spec as GQL Client.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, spec=Client)

    transport = MagicMock()


def test_gql_init(sentry_init):
    """
    Integration test to ensure we can initialize the SDK with the GQL Integration
    """
    sentry_init(integrations=[GQLIntegration()])


def test_setup_once_patches_execute_and_patched_function_calls_original():
    """
    Unit test which ensures the following:
        1. The GQLIntegration setup_once function patches the gql.Client.execute method
        2. The patched gql.Client.execute method still calls the original method, and it
           forwards its arguments to the original method.
        3. The patched gql.Client.execute method returns the same value that the original
           method returns.
    """
    original_method_return_value = MagicMock()

    class OriginalMockClient(_MockClientBase):
        """
        This mock client always returns the mock original_method_return_value when a query
        is executed. This can be used to simulate successful GraphQL queries.
        """

        execute = MagicMock(
            spec=Client.execute, return_value=original_method_return_value
        )

    original_execute_method = OriginalMockClient.execute

    with patch(
        "sentry_sdk.integrations.gql.gql.Client", new=OriginalMockClient
    ) as PatchedMockClient:
        # Below line should patch the PatchedMockClient with Sentry SDK magic
        GQLIntegration.setup_once()

        # We expect GQLIntegration.setup_once to patch the execute method.
        assert (
            PatchedMockClient.execute is not original_execute_method
        ), "execute method not patched"

        # Now, let's instantiate a client and send it a query. Original execute still should get called.
        mock_query = MagicMock(spec=DocumentNode)
        client_instance = PatchedMockClient()
        patched_method_return_value = client_instance.execute(mock_query)

    # Here, we check that the original execute was called
    original_execute_method.assert_called_once_with(client_instance, mock_query)

    # Also, let's verify that the patched execute returns the expected value.
    assert (
        patched_method_return_value is original_method_return_value
    ), "pathced execute method returns a different value than the original execute method"


@patch("sentry_sdk.integrations.gql.Hub.current.capture_event")
def test_patched_gql_execute_captures_and_reraises_graphql_exception(
    mock_capture_event,
):
    """
    Unit test which ensures that in the case that calling the execute method results in a
    TransportQueryError (which gql raises when a GraphQL error occurs), the patched method
    captures the event on the current Hub and it reraises the error.
    """

    class OriginalMockClient(_MockClientBase):
        """
        This mock client always raises a TransportQueryError when a GraphQL query is attempted.
        This simulates a GraphQL query which results in errors.
        """

        execute = MagicMock(
            spec=Client.execute, side_effect=TransportQueryError("query failed")
        )

    with patch(
        "sentry_sdk.integrations.gql.gql.Client", new=OriginalMockClient
    ) as PatchedMockClient:
        # Below line should patch the PatchedMockClient with Sentry SDK magic
        GQLIntegration.setup_once()

        mock_query = MagicMock(spec=DocumentNode)
        client_instance = PatchedMockClient()

        # The error should still get raised even though we have instrumented the execute method.
        with pytest.raises(TransportQueryError):
            client_instance.execute(mock_query)

    # However, we should have also captured the error on the hub.
    mock_capture_event.assert_called_once()

    # Let's also ensure the event captured was a TransportQueryError
    event, _ = mock_capture_event.call_args.args
    (exception,) = event["exception"]["values"]

    assert (
        exception["type"] == "TransportQueryError"
    ), f"{exception['type']} was captured, but we expected a TransportQueryError"


@responses.activate
def test_real_gql_request_no_error(sentry_init, capture_events):
    """
    Integration test verifying that the GQLIntegration works as expected with successful query.
    """
    sentry_init(integrations=[GQLIntegration()])
    events = capture_events()

    url = "http://example.com/graphql"
    query_string = """
        query Example {
            example
        }
    """
    response_data = {"example": "This is the example"}

    # Mock the GraphQL server response
    responses.add(
        method=responses.POST,
        url=url,
        json={"data": {"example": "This is the example"}},
        status=200,
    )

    transport = RequestsHTTPTransport(url=url)
    client = Client(transport=transport)
    query = gql(query_string)

    result = client.execute(query)

    assert (
        result == response_data
    ), "client.execute returned a different value from what it received from the server"
    assert (
        len(events) == 0
    ), "the sdk captured an event, even though the query was successful"


@responses.activate
def test_real_gql_request_with_error(sentry_init, capture_events):
    """
    Integration test verifying that the GQLIntegration works as expected with query resulting
    in a GraphQL error.
    """
    sentry_init(integrations=[GQLIntegration()])
    events = capture_events()

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
        json={"errors": ["something bad happened"]},
        status=200,
    )

    transport = RequestsHTTPTransport(url=url)
    client = Client(transport=transport)
    query = gql(query_string)

    with pytest.raises(TransportQueryError):
        client.execute(query)

    assert (
        len(events) == 1
    ), f"the sdk captured {len(events)} events, but 1 event was expected"

    (event,) = events
    (exception,) = event["exception"]["values"]

    assert (
        exception["type"] == "TransportQueryError"
    ), f"{exception['type']} was captured, but we expected a TransportQueryError"
