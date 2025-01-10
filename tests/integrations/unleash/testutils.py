from contextlib import contextmanager
from UnleashClient import UnleashClient


@contextmanager
def mock_unleash_client():
    """
    Temporarily replaces UnleashClient's methods with mock implementations
    for testing.

    This context manager swaps out UnleashClient's __init__, is_enabled,
    and get_variant methods with mock versions from MockUnleashClient.
    Original methods are restored when exiting the context.

    After mocking the client class the integration can be initialized.
    The methods on the mock client class are overridden by the
    integration and flag tracking proceeds as expected.

    Example:
        with mock_unleash_client():
            client = UnleashClient()  # Uses mock implementation
            sentry_init(integrations=[UnleashIntegration()])
    """
    old_init = UnleashClient.__init__
    old_is_enabled = UnleashClient.is_enabled
    old_get_variant = UnleashClient.get_variant

    UnleashClient.__init__ = MockUnleashClient.__init__
    UnleashClient.is_enabled = MockUnleashClient.is_enabled
    UnleashClient.get_variant = MockUnleashClient.get_variant

    yield

    UnleashClient.__init__ = old_init
    UnleashClient.is_enabled = old_is_enabled
    UnleashClient.get_variant = old_get_variant


class MockUnleashClient:

    def __init__(self, *a, **kw):
        self.features = {
            "hello": True,
            "world": False,
        }

        self.feature_to_variant = {
            "string_feature": {
                "name": "variant1",
                "enabled": True,
                "feature_enabled": True,
                "payload": {"type": "string", "value": "val1"},
            },
            "json_feature": {
                "name": "variant1",
                "enabled": True,
                "feature_enabled": True,
                "payload": {"type": "json", "value": '{"key1": 0.53}'},
            },
            "number_feature": {
                "name": "variant1",
                "enabled": True,
                "feature_enabled": True,
                "payload": {"type": "number", "value": "134.5"},
            },
            "csv_feature": {
                "name": "variant1",
                "enabled": True,
                "feature_enabled": True,
                "payload": {"type": "csv", "value": "abc 123\ncsbq 94"},
            },
            "no_payload_feature": {
                "name": "variant1",
                "enabled": True,
                "feature_enabled": True,
            },
            "no_variant_feature": {
                "name": "disabled",
                "enabled": False,
                "feature_enabled": True,
            },
        }

        self.nonexistent_variant = {"name": "disabled", "enabled": False}

    def is_enabled(self, feature, *a, **kw):
        return self.features.get(feature, False)

    def get_variant(self, feature, *a, **kw):
        return self.feature_to_variant.get(feature, self.nonexistent_variant)
