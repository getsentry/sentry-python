from functools import wraps

import sentry_sdk
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.consts import OP
from sentry_sdk.utils import logger, qualname_from_function


if TYPE_CHECKING:
    from typing import Any, Optional

    from sentry_sdk.tracing import Transaction


def start_child_span_decorator(func):
    # type: (Any) -> Any
    """
    Decorator to add child spans for functions.

    This is the Python 2 compatible version of the decorator.
    Duplicated code from ``sentry_sdk.tracing_utils_python3.start_child_span_decorator``.

    See also ``sentry_sdk.tracing.trace()``.
    """

    def _get_transaction():
        # type: () -> Optional[Transaction]
        transaction = sentry_sdk.Hub.current.scope.transaction
        return transaction

    @wraps(func)
    def func_with_tracing(*args, **kwargs):
        # type: (*Any, **Any) -> Any

        transaction = _get_transaction()

        # If no transaction, do nothing
        if transaction is None:
            logger.warning(
                "No transaction found. Not creating a child span for %s. "
                "Please start a Sentry transaction before calling this function.",
                qualname_from_function(func),
            )
            return func(*args, **kwargs)

        # If we have a transaction, we decorate the function!
        with transaction.start_child(
            op=OP.FUNCTION,
            description=qualname_from_function(func),
        ):
            return func(*args, **kwargs)

    return func_with_tracing
