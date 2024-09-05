from opentelemetry.context import Context, get_value, set_value
from opentelemetry.context.contextvars_context import ContextVarsRuntimeContext

import sentry_sdk
from sentry_sdk.integrations.opentelemetry.consts import (
    SENTRY_SCOPES_KEY,
    SENTRY_FORK_ISOLATION_SCOPE_KEY,
    SENTRY_USE_CURRENT_SCOPE_KEY,
    SENTRY_USE_ISOLATION_SCOPE_KEY,
)


class SentryContextVarsRuntimeContext(ContextVarsRuntimeContext):
    def attach(self, context):
        # type: (Context) -> object
        scopes = get_value(SENTRY_SCOPES_KEY, context)
        should_fork_isolation_scope = context.pop(
            SENTRY_FORK_ISOLATION_SCOPE_KEY, False
        )
        should_use_isolation_scope = context.pop(SENTRY_USE_ISOLATION_SCOPE_KEY, None)
        should_use_current_scope = context.pop(SENTRY_USE_CURRENT_SCOPE_KEY, None)

        if scopes and isinstance(scopes, tuple):
            (current_scope, isolation_scope) = scopes
        else:
            current_scope = sentry_sdk.get_current_scope()
            isolation_scope = sentry_sdk.get_isolation_scope()

        if should_use_current_scope:
            new_scope = should_use_current_scope
        else:
            new_scope = current_scope.fork()

        if should_use_isolation_scope:
            new_isolation_scope = should_use_isolation_scope
        elif should_fork_isolation_scope:
            new_isolation_scope = isolation_scope.fork()
        else:
            new_isolation_scope = isolation_scope

        new_scopes = (new_scope, new_isolation_scope)

        new_context = set_value(SENTRY_SCOPES_KEY, new_scopes, context)
        return super().attach(new_context)
