from typing import TYPE_CHECKING
import sentry_sdk

from sentry_sdk.flag_utils import FlagManager
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

    def __init__(self, capacity):
        # type: (int) -> None
        self.flag_manager = FlagManager(capacity=capacity)

        # Get or create a new isolation scope and register the integration's
        # error processing hook on it.
        scope = sentry_sdk.get_isolation_scope()
        scope.add_error_processor(self.error_processor)

        # This is a globally registered hook (its a list singleton). FlagManager
        # expects itself to be in a THREAD-LOCAL context. Whatever hooks are
        # triggered will not be THREAD-LOCAL unless we seed the open feature hook
        # class with thread-local context.
        api.add_hooks(hooks=[OpenFeatureHook(self.flag_manager)])

    def error_processor(self, event, exc_info):
        """
        On error Sentry will call this hook. This needs to serialize the flags
        from the THREAD-LOCAL context and put the result into the error event.
        """
        event["contexts"]["flags"] = {"values": self.flag_manager.get_flags()}
        return event


class OpenFeatureHook(Hook):
    """
    OpenFeature will call the `after` method after each flag evaluation. We need to
    accept the method call and push the result into our THREAD-LOCAL buffer.
    """

    def __init__(self, flag_manager):
        # type: (FlagManager) -> None
        self.flag_manager = flag_manager

    def after(self, hook_context, details, hints) -> None:
        # type: (HookContext, FlagEvaluationDetails, HookHints) -> None
        if isinstance(details.value, bool):
            self.flag_manager.set_flag(details.flag_key, details.value)
