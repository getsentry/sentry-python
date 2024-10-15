from typing import TYPE_CHECKING
import sentry_sdk

from sentry_sdk.integrations import DidNotEnable, Integration

try:
    from openfeature import api
    from openfeature.hook import Hook
    from sentry_sdk._types import Event, ExcInfo
    from typing import Optional

    if TYPE_CHECKING:
        from openfeature.flag_evaluation import FlagEvaluationDetails
        from openfeature.hook import HookContext, HookHints
except ImportError:
    raise DidNotEnable("OpenFeature is not installed")


class OpenFeatureIntegration(Integration):
    identifier = "openfeature"

    @staticmethod
    def setup_once():
        # type: () -> None
        def error_processor(event, exc_info):
            # type: (Event, ExcInfo) -> Optional[Event]
            scope = sentry_sdk.get_current_scope()
            event["contexts"]["flags"] = {"values": scope.flags.get()}
            return event

        scope = sentry_sdk.get_current_scope()
        scope.add_error_processor(error_processor)

        # Register the hook within the global openfeature hooks list.
        api.add_hooks(hooks=[OpenFeatureHook()])


class OpenFeatureHook(Hook):  # type: ignore

    def after(self, hook_context, details, hints):
        # type: (HookContext, FlagEvaluationDetails, HookHints) -> None
        if isinstance(details.value, bool):
            flags = sentry_sdk.get_current_scope().flags
            flags.set(details.flag_key, details.value)

    def error(self, hook_context, exception, hints):
        # type: (HookContext, Exception, HookHints) -> None
        if isinstance(hook_context.default_value, bool):
            flags = sentry_sdk.get_current_scope().flags
            flags.set(hook_context.flag_key, hook_context.default_value)
