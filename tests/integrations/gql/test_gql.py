import pytest

gql = pytest.importorskip("gql")

from gql import Client
from gql.transport.exceptions import TransportQueryError
from graphql import DocumentNode
from sentry_sdk.integrations.gql import GQLIntegration
from unittest.mock import MagicMock, Mock, patch


class _MockClientBase(MagicMock):
    """
    Mocked version of GQL Client class, following same spec as GQL Client.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, spec=Client)

    transport = MagicMock()


def test_gql_init(sentry_init):
    sentry_init(integrations=[GQLIntegration()])


@patch("sentry_sdk.integrations.gql.gql")
def test_setup_once_patches_gql_execute(mock_gql):
    GQLIntegration.setup_once()

    assert not isinstance(
        mock_gql.Client.execute, Mock
    ), "setup_once did not patch gql.Client.execute"

    assert callable(
        mock_gql.Client.execute
    ), "the patched gql.Client.execute is not a function"


def test_patched_gql_execute_still_calls_real_execute():
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

        original_execute_method.assert_called_once_with(client_instance, mock_query)

        # Also, let's verify that the patched execute returns the expected value.
        assert (
            patched_method_return_value is original_method_return_value
        ), "pathced execute method returns a different value than the original execute method"


@patch("sentry_sdk.integrations.gql.Hub.current.capture_event")
def test_patched_gql_execute_captures_and_reraises_graphql_exception(
    mock_capture_event,
):
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
