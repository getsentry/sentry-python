import pytest

gql = pytest.importorskip("gql")

from gql.transport.exceptions import TransportQueryError
from sentry_sdk.integrations.gql import GQLIntegration
from unittest.mock import MagicMock, Mock, patch


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


@patch("sentry_sdk.integrations.gql.gql")
def test_patched_gql_execute_still_calls_real_execute(mock_gql):
    class MockClient(MagicMock):
        def execute(self, _, x, y):
            return 2 * x + y

    mock_gql.Client = MockClient
    call_args = (None, 3, 4)

    client_before_patch = MockClient()
    result_before_patch = client_before_patch.execute(*call_args)

    GQLIntegration.setup_once()

    client_after_patch = mock_gql.Client()
    result_after_patch = client_after_patch.execute(*call_args)

    assert (
        result_after_patch == result_before_patch
    ), "the patched execute returned a different result than the real execute"


@patch("sentry_sdk.integrations.gql.print_ast")
@patch("sentry_sdk.integrations.gql.event_from_exception")
@patch("sentry_sdk.integrations.gql.Hub")
@patch("sentry_sdk.integrations.gql.gql")
def test_patched_gql_execute_captures_and_reraises_graphql_exception(
    mock_gql, mock_hub, mock_event_from_exception, _
):
    mock_event_from_exception.return_value = (MagicMock(), MagicMock())

    class MockClient(MagicMock):
        def execute(self, _):
            raise TransportQueryError("the query failed")

    mock_gql.Client = MockClient

    GQLIntegration.setup_once()

    mock_client = mock_gql.Client()

    with pytest.raises(TransportQueryError):
        mock_client.execute(MagicMock())

    assert mock_hub.current.capture_event.called, "event was not captured"
