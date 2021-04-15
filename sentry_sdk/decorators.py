import logging
from typing import Optional
from functools import wraps
import inspect

import sentry_sdk

logger = logging.getLogger(__name__)


def sentry_traced(
    func=None,
    *,
    transaction_name: Optional[str] = None,
    op: Optional[str] = None,
):
    """
    Function decorator to start a child under the existing current transaction or
    under a new transaction, if it doesn't exist.

    Args:
        transaction_name: the name of the new transaction if no transaction already
            exists. If a transaction is found in current scope, this name is ignored.
            If no transaction is found and no transaction name is provided, no action
            is taken.
        op: the name of the child. Defaults to the decorated function name.

    Returns:
        a decorated function executing within a Sentry transaction child

    Usage:
    @sentry_traced
    def my_function():
        ...

    @sentry_traced(transaction_name="new_tx")
    async def my_async_function():
        ...

    @sentry_trace(op="child_name")
    def my_function():
       ...
    """

    def start_child_decorator(func):
        def _transaction_and_op():
            # Set transaction
            transaction = sentry_sdk.Hub.current.scope.transaction
            # If no current transaction we create one
            if transaction_name and transaction is None:
                transaction = sentry_sdk.start_transaction(name=transaction_name)
            # Child name - defaults to the decorated function name
            op_ = op or func.__name__
            return transaction, op_

        # Asynchronous case
        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def func_with_tracing(*args, **kwargs):
                transaction, op_ = _transaction_and_op()
                # If no transaction, do nothing
                if transaction is None:
                    return await func(*args, **kwargs)
                # If we have a transaction, we decorate the function!
                with transaction.start_child(op=op_):
                    return await func(*args, **kwargs)

        # Synchronous case
        else:

            @wraps(func)
            def func_with_tracing(*args, **kwargs):
                transaction, op_ = _transaction_and_op()
                # If no transaction, do nothing
                if transaction is None:
                    return func(*args, **kwargs)
                # If we have a transaction, we decorate the function!
                with transaction.start_child(op=op_):
                    return func(*args, **kwargs)

        return func_with_tracing

    # This patterns allows usage of both @sentry_traced and @sentry_traced(...)
    # See https://stackoverflow.com/questions/52126071/decorator-with-arguments-avoid-parenthesis-when-no-arguments/52126278
    if func:
        return start_child_decorator(func)
    else:
        return start_child_decorator
