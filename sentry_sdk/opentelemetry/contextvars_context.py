from typing import cast, TYPE_CHECKING

from opentelemetry.trace import get_current_span, set_span_in_context
from opentelemetry.trace.span import INVALID_SPAN
from opentelemetry.context import Context, get_value, set_value
from opentelemetry.context.contextvars_context import ContextVarsRuntimeContext

import sentry_sdk
from sentry_sdk.tracing import Span
from sentry_sdk.opentelemetry.consts import (
    SENTRY_SCOPES_KEY,
    SENTRY_FORK_ISOLATION_SCOPE_KEY,
    SENTRY_USE_CURRENT_SCOPE_KEY,
    SENTRY_USE_ISOLATION_SCOPE_KEY,
)

if TYPE_CHECKING:
    from typing import Optional
    from contextvars import Token
    import sentry_sdk.opentelemetry.scope as scope


class SentryContextVarsRuntimeContext(ContextVarsRuntimeContext):
    def attach(self, context: "Context") -> "Token[Context]":
        scopes = get_value(SENTRY_SCOPES_KEY, context)

        should_fork_isolation_scope = context.pop(
            SENTRY_FORK_ISOLATION_SCOPE_KEY, False
        )
        should_fork_isolation_scope = cast("bool", should_fork_isolation_scope)

        should_use_isolation_scope = context.pop(SENTRY_USE_ISOLATION_SCOPE_KEY, None)
        should_use_isolation_scope = cast(
            "Optional[scope.PotelScope]", should_use_isolation_scope
        )

        should_use_current_scope = context.pop(SENTRY_USE_CURRENT_SCOPE_KEY, None)
        should_use_current_scope = cast(
            "Optional[scope.PotelScope]", should_use_current_scope
        )

        if scopes:
            scopes = cast("tuple[scope.PotelScope, scope.PotelScope]", scopes)
            (current_scope, isolation_scope) = scopes
        else:
            current_scope = sentry_sdk.get_current_scope()
            isolation_scope = sentry_sdk.get_isolation_scope()

        new_context = context

        if should_use_current_scope:
            new_scope = should_use_current_scope

            # the main case where we use use_scope is for
            # scope propagation in the ThreadingIntegration
            # so we need to carry forward the span reference explicitly too
            span = should_use_current_scope.span
            if span:
                new_context = set_span_in_context(span._otel_span, new_context)

        else:
            new_scope = current_scope.fork()

            # carry forward a wrapped span reference since the otel context is always the
            # source of truth for the active span
            current_span = get_current_span(context)
            if current_span != INVALID_SPAN:
                new_scope._span = Span(otel_span=get_current_span(context))

        if should_use_isolation_scope:
            new_isolation_scope = should_use_isolation_scope
        elif should_fork_isolation_scope:
            new_isolation_scope = isolation_scope.fork()
        else:
            new_isolation_scope = isolation_scope

        new_scopes = (new_scope, new_isolation_scope)

        new_context = set_value(SENTRY_SCOPES_KEY, new_scopes, new_context)
        return super().attach(new_context)
