from functools import partial, partialmethod

from sentry_sdk.utils import transaction_from_function


class MyClass:
    def myfunc(self):
        pass

    @partialmethod
    def my_partial_method(self):
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
        x(MyClass.my_partial_method)
        == "partialmethod(<function tests.utils.test_transaction.MyClass.my_partial_method>)"
    )
    assert (
        x(my_partial_lambda)
        == "partial(<function tests.utils.test_transaction.<lambda>>)"
    )
