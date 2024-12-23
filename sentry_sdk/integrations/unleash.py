from functools import wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.flag_utils import flag_error_processor
from sentry_sdk.integrations import Integration, DidNotEnable

if TYPE_CHECKING:
    from typing import Any, Optional

    try:
        from UnleashClient import UnleashClient
    except ImportError:
        raise DidNotEnable("UnleashClient is not installed")


class UnleashIntegration(Integration):
    identifier = "unleash"
    _unleash_client = None  # type: Optional[UnleashClient]

    def __init__(self, unleash_client):
        # type: (Optional[UnleashClient]) -> None
        self.__class__._unleash_client = unleash_client

    @staticmethod
    def setup_once():
        # type: () -> None

        client = UnleashIntegration._unleash_client
        if not client:
            raise DidNotEnable("Error getting UnleashClient instance")

        # Wrap and patch evaluation methods (instance methods)
        old_is_enabled = client.is_enabled
        old_get_variant = client.get_variant

        @wraps(old_is_enabled)
        def sentry_is_enabled(feature, *a, **kw):
            # type: (str, *Any, **Any) -> Any
            enabled = old_is_enabled(feature, *a, **kw)

            # We have no way of knowing what type of unleash feature this is, so we have to treat
            # it as a boolean / toggle feature.
            flags = sentry_sdk.get_current_scope().flags
            flags.set(feature, enabled)

            return enabled

        @wraps(old_get_variant)
        def sentry_get_variant(feature, *a, **kw):
            # type: (str, *Any, **Any) -> Any
            variant = old_get_variant(feature, *a, **kw)
            enabled = variant.get("enabled", False)
            # _payload_type = variant.get("payload", {}).get("type")

            # Payloads are not always used as the feature's value for application logic. They
            # may be used for metrics or debugging context instead. Therefore, we treat every
            # variant as a boolean toggle, using the `enabled` field.
            flags = sentry_sdk.get_current_scope().flags
            flags.set(feature, enabled)
            return variant

        client.is_enabled = sentry_is_enabled  # type: ignore
        client.get_variant = sentry_get_variant  # type: ignore

        # Error processor
        scope = sentry_sdk.get_current_scope()
        scope.add_error_processor(flag_error_processor)
