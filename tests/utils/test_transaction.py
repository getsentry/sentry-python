from sentry_sdk.utils import transaction_from_function


class MyClass:
    def myfunc(self):
        pass


def myfunc():
    pass


def test_transaction_from_function():
    x = transaction_from_function
    assert x(MyClass) == "tests.utils.test_transaction.MyClass"
    assert x(MyClass.myfunc) == "tests.utils.test_transaction.MyClass.myfunc"
    assert x(myfunc) == "tests.utils.test_transaction.myfunc"
    assert x(None) is None
    assert x(42) is None
    assert x(lambda: None).endswith("<lambda>")
