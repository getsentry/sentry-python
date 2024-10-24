from typing import TYPE_CHECKING
import sentry_sdk

from sentry_sdk.integrations import DidNotEnable, Integration

try:
    import ldclient
    from ldclient.hook import Hook, Metadata

    if TYPE_CHECKING:
        from ldclient import LDClient
        from ldclient.hook import EvaluationSeriesContext
        from ldclient.evaluation import EvaluationDetail

        from sentry_sdk._types import Event, ExcInfo
        from typing import Any, Optional
except ImportError:
    raise DidNotEnable("LaunchDarkly is not installed")


class LaunchDarklyIntegration(Integration):
    identifier = "launchdarkly"

    def __init__(self, client=None):
        # type: (LDClient | None) -> None
        """
        @param client    An initialized LDClient instance. If a client is not provided, this integration will attempt to
                         use the shared global instance. This will fail if ldclient.set_config() hasn't been called.

        Docs reference: https://docs.launchdarkly.com/sdk/server-side/python
        """
        if client is None:
            try:
                client = ldclient.get()  # global singleton.
            except Exception as exc:
                raise DidNotEnable("Error getting LaunchDarkly client. " + repr(exc))

        if not client.is_initialized():
            raise DidNotEnable("LaunchDarkly client is not initialized.")

        # Register the flag collection hook with the given client.
        client.add_hook(LaunchDarklyHook())

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


class LaunchDarklyHook(Hook):

    @property
    def metadata(self):
        # type: () -> Metadata
        return Metadata(name="sentry-feature-flag-recorder")

    def after_evaluation(self, series_context, data, detail):
        # type: (EvaluationSeriesContext, dict[Any, Any], EvaluationDetail) -> dict[Any, Any]
        if isinstance(detail.value, bool):
            flags = sentry_sdk.get_current_scope().flags
            flags.set(series_context.key, detail.value)
        return data

    def before_evaluation(self, _series_context, data):
        # type: (EvaluationSeriesContext, dict[Any, Any]) -> dict[Any, Any]
        return data  # No-op.
