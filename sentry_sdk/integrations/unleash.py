from functools import wraps
from typing import Any

import sentry_sdk
from sentry_sdk.integrations import Integration, DidNotEnable

try:
    from UnleashClient import UnleashClient
except ImportError:
    raise DidNotEnable("UnleashClient is not installed")


class UnleashIntegration(Integration):
    identifier = "unleash"

    @staticmethod
    def setup_once():
        # type: () -> None
        # Wrap and patch evaluation methods (instance methods)
        old_is_enabled = UnleashClient.is_enabled
        old_get_variant = UnleashClient.get_variant

        @wraps(old_is_enabled)
        def sentry_is_enabled(self, feature, *args, **kwargs):
            # type: (UnleashClient, str, *Any, **Any) -> Any
            enabled = old_is_enabled(self, feature, *args, **kwargs)

            # We have no way of knowing what type of unleash feature this is, so we have to treat
            # it as a boolean / toggle feature.
            flags = sentry_sdk.get_current_scope().flags
            flags.set(feature, enabled)

            return enabled

        @wraps(old_get_variant)
        def sentry_get_variant(self, feature, *args, **kwargs):
            # type: (UnleashClient, str, *Any, **Any) -> Any
            variant = old_get_variant(self, feature, *args, **kwargs)
            enabled = variant.get("enabled", False)

            # Payloads are not always used as the feature's value for application logic. They
            # may be used for metrics or debugging context instead. Therefore, we treat every
            # variant as a boolean toggle, using the `enabled` field.
            flags = sentry_sdk.get_current_scope().flags
            flags.set(feature, enabled)

            return variant

        UnleashClient.is_enabled = sentry_is_enabled  # type: ignore
        UnleashClient.get_variant = sentry_get_variant  # type: ignore
