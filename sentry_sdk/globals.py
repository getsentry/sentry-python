from sentry_sdk.consts import TYPE_CHECKING
from sentry_sdk.utils import ContextVar


if TYPE_CHECKING:
    from typing import Optional
    from sentry_sdk import Scope


# global scope over everything
SENTRY_GLOBAL_SCOPE = None  # type: Optional[Scope]

# created by integrations (where we clone the Hub now)
sentry_isolation_scope = ContextVar("sentry_isolation_scope", default=None)

# cloned for threads/tasks/...
sentry_current_scope = ContextVar("sentry_current_scope", default=None)
