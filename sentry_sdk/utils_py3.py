from sentry_sdk.hub import Hub
from functools import wraps

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Optional
    from sentry_sdk.integrations import Integration

    from sentry_sdk._types import GenericAsyncCallable


def ensure_integration_enabled_async(original_function, integration=None):
    # type: (GenericAsyncCallable, Optional[type[Integration]]) -> Callable[[GenericAsyncCallable], GenericAsyncCallable]
    def patcher(sentry_patched_function):
        # type: (GenericAsyncCallable) -> GenericAsyncCallable
        @wraps(original_function)
        async def runner(*args, **kwargs):
            # type: (*object, **object) -> object
            if (
                integration is not None
                and Hub.current.get_integration(integration) is None
            ):
                return await original_function(*args, **kwargs)

            return await sentry_patched_function(*args, **kwargs)

        return runner

    return patcher
