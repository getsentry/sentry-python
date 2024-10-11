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
    raise DidNotEnable("Starlette is not installed")


class OpenFeatureIntegration(Integration):
    """
    Bridges the sentry and openfeature sdks. Thread-local data is expected to
    flow from openfeature to the integration before the sentry-sdk requests the
    thread-local state to be serialized and sent off in the error payload.
    """

    def __init__(self):
        # type: (int) -> None
        # Store the error processor on the current scope. If its forked
        # (i.e. threads are spawned) the callback will be copied to the
        # new Scope.
        scope = sentry_sdk.get_current_scope()
        scope.add_error_processor(self.error_processor)

        # Register the hook within the global openfeature hooks list.
        api.add_hooks(hooks=[OpenFeatureHook()])

    def error_processor(self, event, exc_info):
        event["contexts"]["flags"] = {
            "values": sentry_sdk.get_current_scope().flags.get()
        }
        return event


class OpenFeatureHook(Hook):

    def after(self, hook_context, details, hints) -> None:
        # type: (HookContext, FlagEvaluationDetails, HookHints) -> None
        if isinstance(details.value, bool):
            flags = sentry_sdk.get_current_scope().flags
            flags.set(details.flag_key, details.value)
