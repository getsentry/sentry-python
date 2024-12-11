from sentry_sdk.flag_utils import flag_error_processor

import sentry_sdk
from sentry_sdk.integrations import Integration


class FeatureFlagsIntegration(Integration):
    """
    Sentry integration for buffering feature flags manually with an API and capturing them on
    error events. We recommend you do this on each flag evaluation. Flags are buffered per Sentry
    scope.

    See the [feature flag documentation](https://develop.sentry.dev/sdk/expected-features/#feature-flags)
    for more information.

    @example
    ```
    import sentry_sdk
    from sentry_sdk.integrations.featureflags import FeatureFlagsIntegration, add_feature_flag

    sentry_sdk.init(dsn="my_dsn", integrations=[FeatureFlagsIntegration()]);

    add_feature_flag('my-flag', true);
    sentry_sdk.capture_exception(Exception('broke')); // 'my-flag' should be captured on this Sentry event.
    ```
    """

    identifier = "featureflags"

    @staticmethod
    def setup_once():
        # type: () -> None
        scope = sentry_sdk.get_current_scope()
        scope.add_error_processor(flag_error_processor)


def add_feature_flag(flag, result):
    # type: (str, bool) -> None
    """
    Records a flag and its value to be sent on subsequent error events. We recommend you do this
    on flag evaluations. Flags are buffered per Sentry scope and limited to 100 per event.
    """
    flags = sentry_sdk.get_current_scope().flags
    flags.set(flag, result)
