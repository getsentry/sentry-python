from opentelemetry.context import Context, get_value, set_value
from opentelemetry.context.contextvars_context import ContextVarsRuntimeContext

import sentry_sdk
from sentry_sdk.integrations.opentelemetry.consts import (
    SENTRY_SCOPES_KEY,
    SENTRY_FORK_ISOLATION_SCOPE_KEY,
)


class SentryContextVarsRuntimeContext(ContextVarsRuntimeContext):
    def attach(self, context):
        # type: (Context) -> object
        scopes = get_value(SENTRY_SCOPES_KEY, context)
        should_fork_isolation_scope = context.pop(
            SENTRY_FORK_ISOLATION_SCOPE_KEY, False
        )

        if scopes and isinstance(scopes, tuple):
            (current_scope, isolation_scope) = scopes
        else:
            current_scope = sentry_sdk.get_current_scope()
            isolation_scope = sentry_sdk.get_isolation_scope()

        new_scope = current_scope.fork()
        new_isolation_scope = (
            isolation_scope.fork() if should_fork_isolation_scope else isolation_scope
        )
        new_scopes = (new_scope, new_isolation_scope)

        new_context = set_value(SENTRY_SCOPES_KEY, new_scopes, context)
        return super().attach(new_context)
