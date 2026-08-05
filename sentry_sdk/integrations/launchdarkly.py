from typing import TYPE_CHECKING

from sentry_sdk.feature_flags import add_feature_flag
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.utils import parse_version

try:
    import ldclient
    from ldclient import __version__ as LDCLIENT_VERSION
    from ldclient.hook import Hook, Metadata

    if TYPE_CHECKING:
        from typing import Any

        from ldclient.evaluation import EvaluationDetail
        from ldclient.hook import EvaluationSeriesContext
except ImportError:
    raise DidNotEnable("LaunchDarkly is not installed")


class LaunchDarklyIntegration(Integration):
    identifier = "launchdarkly"

    @staticmethod
    def setup_once() -> None:
        version = parse_version(LDCLIENT_VERSION)
        _check_minimum_version(LaunchDarklyIntegration, version)

        try:
            client = ldclient.get()
        except Exception as exc:
            raise DidNotEnable("Error getting LaunchDarkly client. " + repr(exc))

        if not client.is_initialized():
            raise DidNotEnable("LaunchDarkly client is not initialized.")

        # Register the flag collection hook with the LD client.
        client.add_hook(LaunchDarklyHook())


class LaunchDarklyHook(Hook):
    @property
    def metadata(self) -> "Metadata":
        return Metadata(name="sentry-flag-auditor")

    def after_evaluation(
        self,
        series_context: "EvaluationSeriesContext",
        data: "dict[Any, Any]",
        detail: "EvaluationDetail",
    ) -> "dict[Any, Any]":
        if isinstance(detail.value, bool):
            add_feature_flag(series_context.key, detail.value)

        return data

    def before_evaluation(
        self, series_context: "EvaluationSeriesContext", data: "dict[Any, Any]"
    ) -> "dict[Any, Any]":
        return data  # No-op.
