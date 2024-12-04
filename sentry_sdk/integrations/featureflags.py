from sentry_sdk.flag_utils import flag_error_processor

import sentry_sdk
from sentry_sdk.integrations import Integration


class FeatureFlagsIntegration(Integration):
    identifier = "featureflags"

    @staticmethod
    def setup_once():
        scope = sentry_sdk.get_current_scope()
        scope.add_error_processor(flag_error_processor)

    @staticmethod
    def set_flag(flag: str, result: bool):
        flags = sentry_sdk.get_current_scope().flags
        flags.set(flag, result)
