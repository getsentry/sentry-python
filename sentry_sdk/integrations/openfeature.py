from typing import TYPE_CHECKING
import sentry_sdk

from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.flag_utils import flag_error_processor

if TYPE_CHECKING:
    from typing import Optional

try:
    from openfeature import api
    from openfeature.hook import Hook

    if TYPE_CHECKING:
        from openfeature.flag_evaluation import FlagEvaluationDetails
        from openfeature.hook import HookContext, HookHints
        from openfeature.client import OpenFeatureClient
except ImportError:
    raise DidNotEnable("OpenFeature is not installed")


class OpenFeatureIntegration(Integration):
    identifier = "openfeature"
    _client = None  # type: Optional[OpenFeatureClient]

    def __init__(self, client=None):
        # type: (Optional[OpenFeatureClient]) -> None
        self.__class__._client = client

    @staticmethod
    def setup_once():
        # type: () -> None

        client = OpenFeatureIntegration._client
        if client:
            # Register the hook within the openfeature client.
            client.add_hooks(hooks=[OpenFeatureHook()])
            print("added hook to", client)
        else:
            # Register the hook within the global openfeature hooks list.
            api.add_hooks(hooks=[OpenFeatureHook()])

        scope = sentry_sdk.get_current_scope()
        scope.add_error_processor(flag_error_processor)


class OpenFeatureHook(Hook):

    def after(self, hook_context, details, hints):
        # type: (HookContext, FlagEvaluationDetails[bool], HookHints) -> None
        if isinstance(details.value, bool):
            flags = sentry_sdk.get_current_scope().flags
            flags.set(details.flag_key, details.value)

    def error(self, hook_context, exception, hints):
        # type: (HookContext, Exception, HookHints) -> None
        if isinstance(hook_context.default_value, bool):
            flags = sentry_sdk.get_current_scope().flags
            flags.set(hook_context.flag_key, hook_context.default_value)
