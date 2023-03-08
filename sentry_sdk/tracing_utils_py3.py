import inspect
from functools import wraps

import sentry_sdk
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.consts import OP
from sentry_sdk.utils import logger, qualname_from_function


if TYPE_CHECKING:
    from typing import Any, Optional, Union

    from sentry_sdk.tracing import Span, Transaction


def _get_running_span_or_transaction():
    # type: () -> Optional[Union[Span, Transaction]]
    current_span = sentry_sdk.Hub.current.scope.span
    if current_span is not None:
        return current_span

    transaction = sentry_sdk.Hub.current.scope.transaction
    return transaction


def start_child_span_decorator(func):
    # type: (Any) -> Any
    """
    Decorator to add child spans for functions.

    This is the Python 3 compatible version of the decorator.
    For Python 2 there is duplicated code here: ``sentry_sdk.tracing_utils_python2.start_child_span_decorator()``.

    See also ``sentry_sdk.tracing.trace()``.
    """

    # Asynchronous case
    if inspect.iscoroutinefunction(func):

        @wraps(func)
        async def func_with_tracing(*args, **kwargs):
            # type: (*Any, **Any) -> Any

            span_or_trx = _get_running_span_or_transaction()

            # If no transaction, do nothing
            if span_or_trx is None:
                logger.warning(
                    "No transaction found. Not creating a child span for %s. "
                    "Please start a Sentry transaction before calling this function.",
                    qualname_from_function(func),
                )
                return await func(*args, **kwargs)

            # If we have a transaction, we wrap the function.
            with span_or_trx.start_child(
                op=OP.FUNCTION,
                description=qualname_from_function(func),
            ):
                return await func(*args, **kwargs)

    # Synchronous case
    else:

        @wraps(func)
        def func_with_tracing(*args, **kwargs):
            # type: (*Any, **Any) -> Any

            transaction = _get_running_span_or_transaction()

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
