from unittest import mock

import pytest

from sentry_sdk.tracing_utils import start_child_span_decorator
from sentry_sdk.utils import logger
from tests.conftest import patch_start_tracing_child


def my_example_function():
    return "return_of_sync_function"


async def my_async_example_function():
    return "return_of_async_function"


def test_trace_decorator():
    with patch_start_tracing_child() as fake_start_child:
        result = my_example_function()
        fake_start_child.assert_not_called()
        assert result == "return_of_sync_function"

        result2 = start_child_span_decorator(my_example_function)()
        fake_start_child.assert_called_once_with(
            op="function", description="test_decorator.my_example_function"
        )
        assert result2 == "return_of_sync_function"


def test_trace_decorator_no_trx():
    with patch_start_tracing_child(fake_transaction_is_none=True):
        with mock.patch.object(logger, "warning", mock.Mock()) as fake_warning:
            result = my_example_function()
            fake_warning.assert_not_called()
            assert result == "return_of_sync_function"

            result2 = start_child_span_decorator(my_example_function)()
            fake_warning.assert_called_once_with(
                "Can not create a child span for %s. "
                "Please start a Sentry transaction before calling this function.",
                "test_decorator.my_example_function",
            )
            assert result2 == "return_of_sync_function"


@pytest.mark.forked
@pytest.mark.asyncio
async def test_trace_decorator_async():
    with patch_start_tracing_child() as fake_start_child:
        result = await my_async_example_function()
        fake_start_child.assert_not_called()
        assert result == "return_of_async_function"

        result2 = await start_child_span_decorator(my_async_example_function)()
        fake_start_child.assert_called_once_with(
            op="function",
            description="test_decorator.my_async_example_function",
        )
        assert result2 == "return_of_async_function"


@pytest.mark.asyncio
async def test_trace_decorator_async_no_trx():
    with patch_start_tracing_child(fake_transaction_is_none=True):
        with mock.patch.object(logger, "warning", mock.Mock()) as fake_warning:
            result = await my_async_example_function()
            fake_warning.assert_not_called()
            assert result == "return_of_async_function"

            result2 = await start_child_span_decorator(my_async_example_function)()
            fake_warning.assert_called_once_with(
                "Can not create a child span for %s. "
                "Please start a Sentry transaction before calling this function.",
                "test_decorator.my_async_example_function",
            )
            assert result2 == "return_of_async_function"
