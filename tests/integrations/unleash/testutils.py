from contextlib import contextmanager
from UnleashClient import UnleashClient


@contextmanager
def mock_unleash_client():
    """
    Temporarily replaces UnleashClient's methods with mock implementations
    for testing.

    This context manager swaps out UnleashClient's __init__ and is_enabled,
    methods with mock versions from MockUnleashClient.
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

    UnleashClient.__init__ = MockUnleashClient.__init__
    UnleashClient.is_enabled = MockUnleashClient.is_enabled

    yield

    UnleashClient.__init__ = old_init
    UnleashClient.is_enabled = old_is_enabled


class MockUnleashClient:
    def __init__(self, *a, **kw):
        self.features = {
            "hello": True,
            "world": False,
        }

    def is_enabled(self, feature, *a, **kw):
        return self.features.get(feature, False)
