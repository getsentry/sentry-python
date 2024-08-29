from typing import cast
from contextlib import contextmanager

from opentelemetry.context import get_value, set_value, attach, detach, get_current
from opentelemetry.trace import SpanContext, NonRecordingSpan, TraceFlags, use_span

from sentry_sdk.scope import Scope, ScopeType
from sentry_sdk.tracing import POTelSpan
from sentry_sdk.integrations.opentelemetry.consts import (
    SENTRY_SCOPES_KEY,
    SENTRY_FORK_ISOLATION_SCOPE_KEY,
)

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Tuple, Optional, Generator, Dict, Any

    from sentry_sdk._types import SamplingContext


class PotelScope(Scope):
    @classmethod
    def _get_scopes(cls):
        # type: () -> Optional[Tuple[Scope, Scope]]
        """
        Returns the current scopes tuple on the otel context. Internal use only.
        """
        return cast("Optional[Tuple[Scope, Scope]]", get_value(SENTRY_SCOPES_KEY))

    @classmethod
    def get_current_scope(cls):
        # type: () -> Scope
        """
        Returns the current scope.
        """
        return cls._get_current_scope() or _INITIAL_CURRENT_SCOPE

    @classmethod
    def _get_current_scope(cls):
        # type: () -> Optional[Scope]
        """
        Returns the current scope without creating a new one. Internal use only.
        """
        scopes = cls._get_scopes()
        return scopes[0] if scopes else None

    @classmethod
    def get_isolation_scope(cls):
        # type: () -> Scope
        """
        Returns the isolation scope.
        """
        return cls._get_isolation_scope() or _INITIAL_ISOLATION_SCOPE

    @classmethod
    def _get_isolation_scope(cls):
        # type: () -> Optional[Scope]
        """
        Returns the isolation scope without creating a new one. Internal use only.
        """
        scopes = cls._get_scopes()
        return scopes[1] if scopes else None

    @contextmanager
    def continue_trace(self, environ_or_headers):
        # type: (Dict[str, Any]) -> Generator[None, None, None]
        self.generate_propagation_context(environ_or_headers)

        span_context = self._incoming_otel_span_context()
        if span_context is None:
            yield
        else:
            with use_span(NonRecordingSpan(span_context)):
                yield

    def _incoming_otel_span_context(self):
        # type: () -> Optional[SpanContext]
        if self._propagation_context is None:
            return None
        # If sentry-trace extraction didn't have a parent_span_id, we don't have an upstream header
        if self._propagation_context.parent_span_id is None:
            return None

        trace_flags = TraceFlags(
            TraceFlags.SAMPLED
            if self._propagation_context.parent_sampled
            else TraceFlags.DEFAULT
        )

        # TODO-neel-potel tracestate
        span_context = SpanContext(
            trace_id=int(self._propagation_context.trace_id, 16),  # type: ignore
            span_id=int(self._propagation_context.parent_span_id, 16),  # type: ignore
            is_remote=True,
            trace_flags=trace_flags,
        )

        return span_context

    def start_span(self, custom_sampling_context=None, **kwargs):
        # type: (Optional[SamplingContext], Any) -> POTelSpan
        # TODO-neel-potel ideally want to remove the span argument, discuss with ivana
        return POTelSpan(**kwargs, scope=self)


_INITIAL_CURRENT_SCOPE = PotelScope(ty=ScopeType.CURRENT)
_INITIAL_ISOLATION_SCOPE = PotelScope(ty=ScopeType.ISOLATION)


@contextmanager
def isolation_scope():
    # type: () -> Generator[Scope, None, None]
    context = set_value(SENTRY_FORK_ISOLATION_SCOPE_KEY, True)
    token = attach(context)
    try:
        yield PotelScope.get_isolation_scope()
    finally:
        detach(token)


@contextmanager
def new_scope():
    # type: () -> Generator[Scope, None, None]
    token = attach(get_current())
    try:
        yield PotelScope.get_current_scope()
    finally:
        detach(token)
