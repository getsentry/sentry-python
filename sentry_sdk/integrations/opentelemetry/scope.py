import warnings

from typing import cast
from contextlib import contextmanager

from opentelemetry.context import get_value, set_value, attach, detach, get_current
from opentelemetry.trace import (
    SpanContext,
    NonRecordingSpan,
    TraceFlags,
    use_span,
)

from sentry_sdk.client import NonRecordingClient
from sentry_sdk.integrations.opentelemetry.consts import (
    SENTRY_SCOPES_KEY,
    SENTRY_FORK_ISOLATION_SCOPE_KEY,
    SENTRY_USE_CURRENT_SCOPE_KEY,
    SENTRY_USE_ISOLATION_SCOPE_KEY,
)
from sentry_sdk.integrations.opentelemetry.utils import trace_state_from_baggage
from sentry_sdk.scope import Scope, ScopeType
from sentry_sdk.tracing import POTelSpan
from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Tuple, Optional, Generator, Dict, Any
    from typing_extensions import Unpack

    import sentry_sdk
    from sentry_sdk._types import SamplingContext
    from sentry_sdk.tracing import TransactionKwargs


# Holds data that will be added to **all** events sent by this process.
# In case this is a http server (think web framework) with multiple users
# the data will be added to events of all users.
# Typically this is used for process wide data such as the release.
_global_scope = None  # type: Optional[Scope]


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

    @classmethod
    def get_global_scope(cls):
        # type: () -> Scope
        """
        .. versionadded:: 2.0.0

        Returns the global scope.
        """
        global _global_scope
        if _global_scope is None:
            _global_scope = PotelScope(ty=ScopeType.GLOBAL)

        return _global_scope

    @classmethod
    def get_client(cls):
        # type: () -> sentry_sdk.client.BaseClient
        """
        .. versionadded:: 2.0.0

        Returns the currently used :py:class:`sentry_sdk.Client`.
        This checks the current scope, the isolation scope and the global scope for a client.
        If no client is available a :py:class:`sentry_sdk.client.NonRecordingClient` is returned.
        """
        scopes = cls._get_scopes()
        if scopes:
            current_scope, isolation_scope = scopes
            try:
                client = current_scope.client
            except AttributeError:
                client = None

            if client is not None and client.is_active():
                return client

            try:
                client = isolation_scope.client
            except AttributeError:
                client = None

            if client is not None and client.is_active():
                return client

        try:
            client = _global_scope.client  # type: ignore
        except AttributeError:
            client = None

        if client is not None and client.is_active():
            return client

        return NonRecordingClient()

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

        # TODO-neel-potel do we need parent and sampled like JS?
        trace_state = None
        if self._propagation_context.baggage:
            trace_state = trace_state_from_baggage(self._propagation_context.baggage)

        span_context = SpanContext(
            trace_id=int(self._propagation_context.trace_id, 16),  # type: ignore
            span_id=int(self._propagation_context.parent_span_id, 16),  # type: ignore
            is_remote=True,
            trace_flags=trace_flags,
            trace_state=trace_state,
        )

        return span_context

    def start_transaction(self, custom_sampling_context=None, **kwargs):
        # type: (Optional[SamplingContext], Unpack[TransactionKwargs]) -> POTelSpan
        """
        .. deprecated:: 3.0.0
            This function is deprecated and will be removed in a future release.
            Use :py:meth:`sentry_sdk.start_span` instead.
        """
        return self.start_span(custom_sampling_context=custom_sampling_context)

    def start_span(self, custom_sampling_context=None, **kwargs):
        # type: (Optional[SamplingContext], Any) -> POTelSpan
        if kwargs.get("description") is not None:
            warnings.warn(
                "The `description` parameter is deprecated. Please use `name` instead.",
                DeprecationWarning,
                stacklevel=2,
            )

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


@contextmanager
def use_scope(scope):
    # type: (Scope) -> Generator[Scope, None, None]
    context = set_value(SENTRY_USE_CURRENT_SCOPE_KEY, scope)
    token = attach(context)

    try:
        yield scope
    finally:
        detach(token)


@contextmanager
def use_isolation_scope(isolation_scope):
    # type: (Scope) -> Generator[Scope, None, None]
    context = set_value(SENTRY_USE_ISOLATION_SCOPE_KEY, isolation_scope)
    token = attach(context)

    try:
        yield isolation_scope
    finally:
        detach(token)
