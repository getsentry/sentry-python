from sentry_sdk.integrations.gql import GQLIntegration
from unittest.mock import Mock, patch


def test_gql_init(sentry_init):
    sentry_init(integrations=[GQLIntegration()])


@patch("sentry_sdk.integrations.gql.gql")
def test_setup_once_patches_gql_execute(patched_gql):
    GQLIntegration.setup_once()

    assert not isinstance(
        patched_gql.Client.execute, Mock
    ), "setup_once did not patch gql.Client.execute"

    assert callable(
        patched_gql.Client.execute
    ), "the patched gql.Client.execute is not a function"
