from unittest import mock
from contextlib import contextmanager
import pytest
import sys

from sentry_sdk.tracing_utils_py3 import (
    start_child_span_decorator as start_child_span_decorator_py3,
)
from sentry_sdk.utils import logger

if sys.version_info < (3, 6):
    pytest.skip("Async decorator only works on Python 3.6+", allow_module_level=True)


class TestClass:
    @staticmethod
    def static(arg):
        return arg

    @classmethod
    def class_(cls, arg):
        return cls, arg


def my_example_function():
    return "return_of_sync_function"


async def my_async_example_function():
    return "return_of_async_function"


@contextmanager
def patch_start_child():
    fake_start_child = mock.MagicMock()
    fake_transaction = mock.MagicMock()
    fake_transaction.start_child = fake_start_child

    with mock.patch(
        "sentry_sdk.tracing_utils_py3.get_current_span",
        return_value=fake_transaction,
    ):
        yield fake_start_child


def test_trace_decorator_sync_py3():
    with patch_start_child() as fake_start_child:
        result = my_example_function()
        fake_start_child.assert_not_called()
        assert result == "return_of_sync_function"

        result2 = start_child_span_decorator_py3(my_example_function)()
        fake_start_child.assert_called_once_with(
            op="function", description="test_decorator_py3.my_example_function"
        )
        assert result2 == "return_of_sync_function"


def test_trace_decorator_sync_py3_no_trx():
    fake_transaction = None

    with mock.patch(
        "sentry_sdk.tracing_utils_py3.get_current_span",
        return_value=fake_transaction,
    ):
        with mock.patch.object(logger, "warning", mock.Mock()) as fake_warning:
            result = my_example_function()
            fake_warning.assert_not_called()
            assert result == "return_of_sync_function"

            result2 = start_child_span_decorator_py3(my_example_function)()
            fake_warning.assert_called_once_with(
                "Can not create a child span for %s. "
                "Please start a Sentry transaction before calling this function.",
                "test_decorator_py3.my_example_function",
            )
            assert result2 == "return_of_sync_function"


@pytest.mark.asyncio
async def test_trace_decorator_async_py3():
    fake_start_child = mock.MagicMock()
    fake_transaction = mock.MagicMock()
    fake_transaction.start_child = fake_start_child

    with mock.patch(
        "sentry_sdk.tracing_utils_py3.get_current_span",
        return_value=fake_transaction,
    ):
        result = await my_async_example_function()
        fake_start_child.assert_not_called()
        assert result == "return_of_async_function"

        result2 = await start_child_span_decorator_py3(my_async_example_function)()
        fake_start_child.assert_called_once_with(
            op="function", description="test_decorator_py3.my_async_example_function"
        )
        assert result2 == "return_of_async_function"


@pytest.mark.asyncio
async def test_trace_decorator_async_py3_no_trx():
    fake_transaction = None

    with mock.patch(
        "sentry_sdk.tracing_utils_py3.get_current_span",
        return_value=fake_transaction,
    ):
        with mock.patch.object(logger, "warning", mock.Mock()) as fake_warning:
            result = await my_async_example_function()
            fake_warning.assert_not_called()
            assert result == "return_of_async_function"

            result2 = await start_child_span_decorator_py3(my_async_example_function)()
            fake_warning.assert_called_once_with(
                "Can not create a child span for %s. "
                "Please start a Sentry transaction before calling this function.",
                "test_decorator_py3.my_async_example_function",
            )
            assert result2 == "return_of_async_function"


def test_staticmethod_patching(sentry_init):
    test_staticmethod_name = "test_decorator_py3.TestClass.static"
    assert (
        ".".join([TestClass.static.__module__, TestClass.static.__qualname__])
        == test_staticmethod_name
    ), "The test static method was moved or renamed. Please update the name accordingly"

    sentry_init(functions_to_trace=[{"qualified_name": test_staticmethod_name}])

    for instance_or_class in (TestClass, TestClass()):
        with patch_start_child() as fake_start_child:
            assert instance_or_class.static(1) == 1
            fake_start_child.assert_called_once()


def test_classmethod_patching(sentry_init):
    test_classmethod_name = "test_decorator_py3.TestClass.class_"
    assert (
        ".".join([TestClass.class_.__module__, TestClass.class_.__qualname__])
        == test_classmethod_name
    ), "The test class method was moved or renamed. Please update the name accordingly"

    sentry_init(functions_to_trace=[{"qualified_name": test_classmethod_name}])

    for instance_or_class in (TestClass, TestClass()):
        with patch_start_child() as fake_start_child:
            assert instance_or_class.class_(1) == (TestClass, 1)
            fake_start_child.assert_called_once()
