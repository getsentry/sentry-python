from opentelemetry.context import Context, create_key, get_value, set_value
from opentelemetry.context.contextvars_context import ContextVarsRuntimeContext

from sentry_sdk.scope import Scope


_SCOPES_KEY = create_key("sentry_scopes")


class SentryContextVarsRuntimeContext(ContextVarsRuntimeContext):
    def attach(self, context):
        # type: (Context) -> object
        scopes = get_value(_SCOPES_KEY, context)

        if scopes and isinstance(scopes, tuple):
            (current_scope, isolation_scope) = scopes
        else:
            current_scope = Scope.get_current_scope()
            isolation_scope = Scope.get_isolation_scope()

        # TODO-neel-potel fork isolation_scope too like JS
        # once we setup our own apis to pass through to otel
        new_scopes = (current_scope.fork(), isolation_scope)
        new_context = set_value(_SCOPES_KEY, new_scopes, context)

        return super().attach(new_context)
