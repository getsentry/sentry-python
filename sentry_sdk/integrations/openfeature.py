from typing import TYPE_CHECKING
import sentry_sdk

from sentry_sdk.flag_utils import FlagBuffer
from sentry_sdk.integrations import DidNotEnable, Integration

try:
    from openfeature import api
    from openfeature.hook import Hook

    if TYPE_CHECKING:
        from openfeature.flag_evaluation import FlagEvaluationDetails
        from openfeature.hook import HookContext, HookHints
        from sentry_sdk._types import Event, ExcInfo
        from typing import Optional
except ImportError:
    raise DidNotEnable("OpenFeature is not installed")


DEFAULT_FLAG_CAPACITY = 100


class OpenFeatureIntegration(Integration):
    identifier = "openfeature"

    def __init__(self, max_flags=DEFAULT_FLAG_CAPACITY):
        # type: (OpenFeatureIntegration, int) -> None
        self._max_flags = max_flags
        self._flags = None  # type: Optional[FlagBuffer]

    @staticmethod
    def setup_once():
        # type: () -> None
        # Register the hook within the global openfeature hooks list.
        api.add_hooks(hooks=[OpenFeatureHook()])

    @property
    def flags(self):
        # type: () -> FlagBuffer
        if self._flags is None:
            max_flags = self._max_flags or DEFAULT_FLAG_CAPACITY
            self._flags = FlagBuffer(capacity=max_flags)
        return self._flags


class OpenFeatureHook(Hook):
    def after(self, hook_context, details, hints):
        # type: (HookContext, FlagEvaluationDetails[bool], HookHints) -> None
        integration = sentry_sdk.get_client().get_integration(OpenFeatureIntegration)
        if integration is None:
            return

        if isinstance(details.value, bool):
            integration.flags.set(details.flag_key, details.value)

    def error(self, hook_context, exception, hints):
        # type: (HookContext, Exception, HookHints) -> None
        integration = sentry_sdk.get_client().get_integration(OpenFeatureIntegration)
        if integration is None:
            return

        def error_processor(event, exc_info):
            # type: (Event, ExcInfo) -> Optional[Event]
            event["contexts"]["flags"] = {"values": integration.flags.get()}
            return event

        scope = sentry_sdk.get_current_scope()
        scope.add_error_processor(error_processor)

        if isinstance(hook_context.default_value, bool):
            integration.flags.set(hook_context.flag_key, hook_context.default_value)
