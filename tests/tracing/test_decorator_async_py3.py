from unittest import mock
import pytest
import sys

from tests.conftest import patch_start_tracing_child

from sentry_sdk.tracing_utils_py3 import (
    start_child_span_decorator as start_child_span_decorator_py3,
)
from sentry_sdk.utils import logger

if sys.version_info < (3, 6):
    pytest.skip("Async decorator only works on Python 3.6+", allow_module_level=True)


async def my_async_example_function():
    return "return_of_async_function"


@pytest.mark.asyncio
async def test_trace_decorator_async_py3():
    with patch_start_tracing_child() as fake_start_child:
        result = await my_async_example_function()
        fake_start_child.assert_not_called()
        assert result == "return_of_async_function"

        result2 = await start_child_span_decorator_py3(my_async_example_function)()
        fake_start_child.assert_called_once_with(
            op="function",
            description="test_decorator_async_py3.my_async_example_function",
        )
        assert result2 == "return_of_async_function"


@pytest.mark.asyncio
async def test_trace_decorator_async_py3_no_trx():
    with patch_start_tracing_child(fake_transaction_is_none=True):
        with mock.patch.object(logger, "warning", mock.Mock()) as fake_warning:
            result = await my_async_example_function()
            fake_warning.assert_not_called()
            assert result == "return_of_async_function"

            result2 = await start_child_span_decorator_py3(my_async_example_function)()
            fake_warning.assert_called_once_with(
                "Can not create a child span for %s. "
                "Please start a Sentry transaction before calling this function.",
                "test_decorator_async_py3.my_async_example_function",
            )
            assert result2 == "return_of_async_function"
