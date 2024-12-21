from functools import wraps

import sentry_sdk
from sentry_sdk.integrations import Integration

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional
    from UnleashClient import UnleashClient


class UnleashIntegration(Integration):
    identifier = "unleash"

    def __init__(self, unleash_client):
        # type: (Optional[UnleashClient]) -> None
        self.unleash_client = unleash_client

    @staticmethod
    def setup_once():
        # type: () -> None
        integration = sentry_sdk.get_client().get_integration(UnleashIntegration)
        if integration is None:
            return

        unleash_client = integration.unleash_client
        old_is_enabled = unleash_client.is_enabled

        @wraps(old_is_enabled)
        def sentry_is_enabled(self, *a, **kw):
            # TODO: # type:
            key, value = a[0], a[1]  # TODO: this is a placeholder
            if isinstance(value, bool):
                flags = sentry_sdk.get_current_scope().flags
                flags.set(key, value)
            return old_is_enabled(self, *a, **kw)

        unleash_client.is_enabled = sentry_is_enabled  # type: ignore

        # TODO: get_variant
