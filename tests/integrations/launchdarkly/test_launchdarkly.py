import sentry_sdk

from ldclient import LDClient
from ldclient.config import Config
from ldclient.context import Context
from ldclient.integrations.test_data import TestData

from sentry_sdk.integrations.launchdarkly import LaunchDarklyIntegration

# Ref: https://docs.launchdarkly.com/sdk/features/test-data-sources#python


def test_launchdarkly_integration(sentry_init):
    td = TestData.data_source()
    client = LDClient(config=Config("sdk-key", update_processor_class=td))
    sentry_init(integrations=[LaunchDarklyIntegration(client=client)])

    # Set test values
    td.update(td.flag("hello").variation_for_all(True))
    td.update(td.flag("world").variation_for_all(True))
    td.update(td.flag("goodbye").variation_for_all(False))

    # Evaluate
    client.variation("hello", Context.create("my-org", "organization"), False)
    client.variation("world", Context.create("user1", "user"), False)
    client.variation("goodbye", Context.create("user2", "user"), False)

    assert sentry_sdk.get_current_scope().flags.get() == [
        {"flag": "hello", "result": True},
        {"flag": "world", "result": True},
        {"flag": "goodbye", "result": False},
    ]
