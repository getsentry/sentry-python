from typing import TYPE_CHECKING
import sentry_sdk

from sentry_sdk.integrations import DidNotEnable, Integration

try:
    import ldclient
    from ldclient.hook import Hook

    if TYPE_CHECKING:
        from ldclient import LDClient
        from ldclient.hook import EvaluationSeriesContext, Metadata
        from ldclient.evaluation import EvaluationDetail

        from sentry_sdk._types import Event, ExcInfo
        from typing import Any, Optional
except ImportError:
    raise DidNotEnable("LaunchDarkly is not installed")


def _get_ldclient():
    # type: () -> LDClient
    try:
        client = ldclient.get()
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise DidNotEnable("Failed to find LaunchDarkly client instance. " + str(exc))

    if client and client.is_initialized():
        return client
    raise DidNotEnable("LaunchDarkly client is not initialized")


class LaunchDarklyIntegration(Integration):
    identifier = "launchdarkly"

    @staticmethod
    def setup_once():
        # type: () -> None
        def error_processor(event, _exc_info):
            # type: (Event, ExcInfo) -> Optional[Event]
            scope = sentry_sdk.get_current_scope()
            event["contexts"]["flags"] = {"values": scope.flags.get()}
            return event

        scope = sentry_sdk.get_current_scope()
        scope.add_error_processor(error_processor)

        # Register the hook with the global launchdarkly client.
        client = _get_ldclient()
        client.add_hook(LaunchDarklyHook())


class LaunchDarklyHook(Hook):

    @property
    def metadata(self):
        # type: () -> Metadata
        return Metadata(name="sentry-on-error-hook")

    def after_evaluation(self, series_context, data, detail):
        # type: (EvaluationSeriesContext, dict[Any, Any], EvaluationDetail) -> dict[Any, Any]
        if isinstance(detail.value, bool):
            flags = sentry_sdk.get_current_scope().flags
            flags.set(series_context.key, detail.value)
        return data

    def before_evaluation(self, _series_context, data):
        # type: (EvaluationSeriesContext, dict[Any, Any]) -> dict[Any, Any]
        return data  # No-op.
