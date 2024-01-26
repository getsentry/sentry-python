from __future__ import annotations

import sys
from functools import wraps

from sentry_sdk.hub import Hub
from sentry_sdk.utils import event_from_exception, reraise
from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from typing import Callable
    from typing import TypeVar
    from typing import Union
    from typing import Optional

    from typing import overload

    F = TypeVar("F", bound=Callable[..., Any])

else:

    def overload(x: F) -> F:
        return x


@overload
def serverless_function(f: F, flush: bool = True) -> F:
    pass


@overload
def serverless_function(  # noqa: F811
    f: None = None, flush: bool = True
) -> Callable[[F], F]:
    pass


def serverless_function(  # noqa: F811
    f: Optional[F] = None, flush: bool = True
) -> Union[F, Callable[[F], F]]:
    def wrapper(f: F) -> F:
        @wraps(f)
        def inner(*args: Any, **kwargs: Any) -> Any:
            with Hub(Hub.current) as hub:
                with hub.configure_scope() as scope:
                    scope.clear_breadcrumbs()

                try:
                    return f(*args, **kwargs)
                except Exception:
                    _capture_and_reraise()
                finally:
                    if flush:
                        _flush_client()

        return inner  # type: ignore

    if f is None:
        return wrapper
    else:
        return wrapper(f)


def _capture_and_reraise() -> None:
    exc_info = sys.exc_info()
    hub = Hub.current
    if hub.client is not None:
        event, hint = event_from_exception(
            exc_info,
            client_options=hub.client.options,
            mechanism={"type": "serverless", "handled": False},
        )
        hub.capture_event(event, hint=hint)

    reraise(*exc_info)


def _flush_client() -> None:
    return Hub.current.flush()
