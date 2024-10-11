from typing import TYPE_CHECKING
import sentry_sdk

from sentry_sdk.integrations import DidNotEnable, Integration

try:
    from openfeature import api
    from openfeature.hook import Hook

    if TYPE_CHECKING:
        from openfeature.flag_evaluation import FlagEvaluationDetails
        from openfeature.hook import HookContext, HookHints
except ImportError:
    raise DidNotEnable("OpenFeature is not installed")


class OpenFeatureIntegration(Integration):

    @staticmethod
    def setup_once():
        def error_processor(event, exc_info):
            scope = sentry_sdk.get_current_scope()
            event["contexts"]["flags"] = {"values": scope.flags.get()}
            return event

        scope = sentry_sdk.get_current_scope()
        scope.add_error_processor(error_processor)

        # Register the hook within the global openfeature hooks list.
        api.add_hooks(hooks=[OpenFeatureHook()])


class OpenFeatureHook(Hook):

    def after(self, hook_context, details, hints) -> None:
        # type: (HookContext, FlagEvaluationDetails, HookHints) -> None
        if isinstance(details.value, bool):
            flags = sentry_sdk.get_current_scope().flags
            flags.set(details.flag_key, details.value)
