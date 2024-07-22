from typing import cast
from contextlib import contextmanager

from opentelemetry.context import get_value, set_value, attach, detach, get_current

from sentry_sdk.scope import Scope, ScopeType
from sentry_sdk.integrations.opentelemetry.consts import (
    SENTRY_SCOPES_KEY,
    SENTRY_FORK_ISOLATION_SCOPE_KEY,
)

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Tuple, Optional, Generator


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
        return cls._get_current_scope() or Scope(ty=ScopeType.CURRENT)

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
        """
        Returns the isolation scope.
        """
        # type: () -> Scope
        return cls._get_isolation_scope() or Scope(ty=ScopeType.ISOLATION)

    @classmethod
    def _get_isolation_scope(cls):
        # type: () -> Optional[Scope]
        """
        Returns the isolation scope without creating a new one. Internal use only.
        """
        scopes = cls._get_scopes()
        return scopes[1] if scopes else None


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
