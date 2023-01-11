import sys
from functools import partial

import pytest

from sentry_sdk.utils import transaction_from_function

try:
    from functools import partialmethod
except ImportError:
    pass


class MyClass:
    def myfunc(self):
        pass


def myfunc():
    pass


@partial
def my_partial():
    pass


my_lambda = lambda: None

my_partial_lambda = partial(lambda: None)


def test_transaction_from_function():
    x = transaction_from_function
    assert x(MyClass) == "tests.utils.test_transaction.MyClass"
    assert x(MyClass.myfunc) == "tests.utils.test_transaction.MyClass.myfunc"
    assert x(myfunc) == "tests.utils.test_transaction.myfunc"
    assert x(None) is None
    assert x(42) is None
    assert x(lambda: None).endswith("<lambda>")
    assert x(my_lambda) == "tests.utils.test_transaction.<lambda>"
    assert (
        x(my_partial) == "partial(<function tests.utils.test_transaction.my_partial>)"
    )
    assert (
        x(my_partial_lambda)
        == "partial(<function tests.utils.test_transaction.<lambda>>)"
    )


@pytest.mark.skipif(sys.version_info < (3, 4), reason="Require python 3.4 or higher")
def test_transaction_from_function_partialmethod():
    x = transaction_from_function

    class MyPartialClass:
        @partialmethod
        def my_partial_method(self):
            pass

    assert (
        x(MyPartialClass.my_partial_method)
        == "partialmethod(<function tests.utils.test_transaction.test_transaction_from_function_partialmethod.<locals>.MyPartialClass.my_partial_method>)"
    )
