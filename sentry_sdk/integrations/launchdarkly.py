from typing import TYPE_CHECKING
import sentry_sdk

from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.flag_utils import flag_error_processor

try:
    import ldclient
    from ldclient.hook import Hook, Metadata

    if TYPE_CHECKING:
        from ldclient import LDClient
        from ldclient.hook import EvaluationSeriesContext
        from ldclient.evaluation import EvaluationDetail

        from typing import Any
except ImportError:
    raise DidNotEnable("LaunchDarkly is not installed")


class LaunchDarklyIntegration(Integration):
    identifier = "launchdarkly"
    _ld_client = None  # type: LDClient | None

    def __init__(self, ld_client=None):
        # type: (LDClient | None) -> None
        """
        :param client: An initialized LDClient instance. If a client is not provided, this
            integration will attempt to use the shared global instance.
        """
        self.__class__._ld_client = ld_client

    @staticmethod
    def setup_once():
        # type: () -> None
        try:
            client = LaunchDarklyIntegration._ld_client or ldclient.get()
        except Exception as exc:
            raise DidNotEnable("Error getting LaunchDarkly client. " + repr(exc))

        # Register the flag collection hook with the LD client.
        client.add_hook(LaunchDarklyHook())

        scope = sentry_sdk.get_current_scope()
        scope.add_error_processor(flag_error_processor)


class LaunchDarklyHook(Hook):

    @property
    def metadata(self):
        # type: () -> Metadata
        return Metadata(name="sentry-flag-auditor")

    def after_evaluation(self, series_context, data, detail):
        # type: (EvaluationSeriesContext, dict[Any, Any], EvaluationDetail) -> dict[Any, Any]
        if isinstance(detail.value, bool):
            flags = sentry_sdk.get_current_scope().flags
            flags.set(series_context.key, detail.value)
        return data

    def before_evaluation(self, series_context, data):
        # type: (EvaluationSeriesContext, dict[Any, Any]) -> dict[Any, Any]
        return data  # No-op.
