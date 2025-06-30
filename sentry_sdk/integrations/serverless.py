from __future__ import annotations
import sys
from functools import wraps

import sentry_sdk
from sentry_sdk.utils import event_from_exception, reraise

from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    from typing import NoReturn
    from typing import Callable
    from typing import TypeVar
    from typing import ParamSpec
    from typing import Union
    from typing import Optional

    T = TypeVar("T")
    P = ParamSpec("P")


if TYPE_CHECKING:

    @overload
    def serverless_function(f: Callable[P, T], flush: bool = True) -> Callable[P, T]:
        pass

    @overload
    def serverless_function(
        f: None = None, flush: bool = True
    ) -> Callable[[Callable[P, T]], Callable[P, T]]:
        pass


def serverless_function(
    f: Optional[Callable[P, T]] = None, flush: bool = True
) -> Union[Callable[P, T], Callable[[Callable[P, T]], Callable[P, T]]]:
    def wrapper(f: Callable[P, T]) -> Callable[P, T]:
        @wraps(f)
        def inner(*args: P.args, **kwargs: P.kwargs) -> T:
            with sentry_sdk.isolation_scope() as scope:
                scope.clear_breadcrumbs()

                try:
                    return f(*args, **kwargs)
                except Exception:
                    _capture_and_reraise()
                finally:
                    if flush:
                        sentry_sdk.flush()

        return inner

    if f is None:
        return wrapper
    else:
        return wrapper(f)


def _capture_and_reraise() -> NoReturn:
    exc_info = sys.exc_info()
    client = sentry_sdk.get_client()
    if client.is_active():
        event, hint = event_from_exception(
            exc_info,
            client_options=client.options,
            mechanism={"type": "serverless", "handled": False},
        )
        sentry_sdk.capture_event(event, hint=hint)

    reraise(*exc_info)
