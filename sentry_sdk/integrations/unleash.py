from functools import wraps

import sentry_sdk
from sentry_sdk.flag_utils import flag_error_processor
from sentry_sdk.integrations import Integration, DidNotEnable

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from typing import Optional, Any
    from UnleashClient import UnleashClient


class UnleashIntegration(Integration):
    identifier = "unleash"
    _unleash_client = None  # type: Optional[UnleashClient]

    def __init__(self, unleash_client):
        # type: (UnleashClient) -> None
        self.__class__._unleash_client = unleash_client

    @staticmethod
    def setup_once():
        # type: () -> None

        # Wrap and patch evaluation functions
        client = UnleashIntegration._unleash_client
        if not client:
            raise DidNotEnable("Error finding UnleashClient instance.")

        # Instance methods (self is excluded)
        old_is_enabled = client.is_enabled
        old_get_variant = client.get_variant

        @wraps(old_is_enabled)
        def sentry_is_enabled(feature, *a, **kw):
            # type: (str, *Any, **Any) -> Any
            enabled = old_is_enabled(feature, *a, **kw)

            # We have no way of knowing what type of feature this is. Have to treat it as
            # boolean flag. TODO: Unless we fetch a list of non-bool flags on startup..
            flags = sentry_sdk.get_current_scope().flags
            flags.set(feature, enabled)

            return enabled

        @wraps(old_get_variant)
        def sentry_get_variant(feature, *a, **kw):
            # type: (str, *Any, **Any) -> Any
            variant = old_get_variant(feature, *a, **kw)
            enabled = variant.get("enabled", False)
            payload_type = variant.get("payload", {}).get("type")

            if payload_type is None or payload_type == "boolean":
                flags = sentry_sdk.get_current_scope().flags
                flags.set(feature, enabled)

            return variant

        client.is_enabled = sentry_is_enabled  # type: ignore
        client.get_variant = sentry_get_variant  # type: ignore

        # Error processor
        scope = sentry_sdk.get_current_scope()
        scope.add_error_processor(flag_error_processor)
