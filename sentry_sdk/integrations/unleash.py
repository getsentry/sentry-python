from functools import wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.flag_utils import flag_error_processor
from sentry_sdk.integrations import Integration

from UnleashClient import UnleashClient

if TYPE_CHECKING:
    from typing import Any


class UnleashIntegration(Integration):
    identifier = "unleash"

    @staticmethod
    def setup_once():
        # type: () -> None

        # Wrap and patch evaluation functions
        old_is_enabled = UnleashClient.is_enabled
        old_get_variant = UnleashClient.get_variant

        @wraps(old_is_enabled)
        def sentry_is_enabled(self, feature, *a, **kw):
            # type: (UnleashClient, str, *Any, **Any) -> Any
            enabled = old_is_enabled(self, feature, *a, **kw)

            # We have no way of knowing what type of feature this is. Have to treat it as
            # boolean flag. TODO: Unless we fetch a list of non-bool flags on startup..
            flags = sentry_sdk.get_current_scope().flags
            flags.set(feature, enabled)

            return enabled

        @wraps(old_get_variant)
        def sentry_get_variant(self, feature, *a, **kw):
            # type: (UnleashClient, str, *Any, **Any) -> Any
            variant = old_get_variant(self, feature, *a, **kw)
            enabled = variant.get("enabled", False)
            payload_type = variant.get("payload", {}).get("type")

            if payload_type is None:
                flags = sentry_sdk.get_current_scope().flags
                flags.set(feature, enabled)

            return variant

        UnleashClient.is_enabled = sentry_is_enabled  # type: ignore
        UnleashClient.get_variant = sentry_get_variant  # type: ignore

        # Error processor
        scope = sentry_sdk.get_current_scope()
        scope.add_error_processor(flag_error_processor)
