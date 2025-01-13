import sentry_sdk


def add_feature_flag(flag, result):
    # type: (str, bool) -> None
    """
    Records a flag and its value to be sent on subsequent error events by FeatureFlagsIntegration.
    We recommend you do this on flag evaluations. Flags are buffered per Sentry scope.
    """
    flags = sentry_sdk.get_current_scope().flags
    flags.set(flag, result)
