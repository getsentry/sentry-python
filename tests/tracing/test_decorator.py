import inspect
from unittest import mock

import pytest

from sentry_sdk.tracing import trace
from sentry_sdk.tracing_utils import create_span_decorator
from sentry_sdk.utils import logger
from tests.conftest import patch_start_tracing_child


def my_example_function():
    return "return_of_sync_function"


async def my_async_example_function():
    return "return_of_async_function"


@pytest.mark.forked
def test_trace_decorator():
    with patch_start_tracing_child() as fake_start_child:
        result = my_example_function()
        fake_start_child.assert_not_called()
        assert result == "return_of_sync_function"

        start_child_span_decorator = create_span_decorator(template="span")
        result2 = start_child_span_decorator(my_example_function)()
        fake_start_child.assert_called_once_with(
            op="function", name="test_decorator.my_example_function"
        )
        assert result2 == "return_of_sync_function"


def test_trace_decorator_no_trx():
    with patch_start_tracing_child(fake_transaction_is_none=True):
        with mock.patch.object(logger, "debug", mock.Mock()) as fake_debug:
            result = my_example_function()
            fake_debug.assert_not_called()
            assert result == "return_of_sync_function"

            start_child_span_decorator = create_span_decorator(template="span")
            result2 = start_child_span_decorator(my_example_function)()
            fake_debug.assert_called_once_with(
                "Cannot create a child span for %s. "
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

        start_child_span_decorator = create_span_decorator(template="span")
        result2 = await start_child_span_decorator(my_async_example_function)()
        fake_start_child.assert_called_once_with(
            op="function",
            name="test_decorator.my_async_example_function",
        )
        assert result2 == "return_of_async_function"


@pytest.mark.asyncio
async def test_trace_decorator_async_no_trx():
    with patch_start_tracing_child(fake_transaction_is_none=True):
        with mock.patch.object(logger, "debug", mock.Mock()) as fake_debug:
            result = await my_async_example_function()
            fake_debug.assert_not_called()
            assert result == "return_of_async_function"

            start_child_span_decorator = create_span_decorator(template="span")
            result2 = await start_child_span_decorator(my_async_example_function)()
            fake_debug.assert_called_once_with(
                "Cannot create a child span for %s. "
                "Please start a Sentry transaction before calling this function.",
                "test_decorator.my_async_example_function",
            )
            assert result2 == "return_of_async_function"


def test_functions_to_trace_signature_unchanged_sync(sentry_init):
    sentry_init(
        traces_sample_rate=1.0,
    )

    def _some_function(a, b, c):
        pass

    @trace
    def _some_function_traced(a, b, c):
        pass

    assert inspect.getcallargs(_some_function, 1, 2, 3) == inspect.getcallargs(
        _some_function_traced, 1, 2, 3
    )


@pytest.mark.asyncio
async def test_functions_to_trace_signature_unchanged_async(sentry_init):
    sentry_init(
        traces_sample_rate=1.0,
    )

    async def _some_function(a, b, c):
        pass

    @trace
    async def _some_function_traced(a, b, c):
        pass

    assert inspect.getcallargs(_some_function, 1, 2, 3) == inspect.getcallargs(
        _some_function_traced, 1, 2, 3
    )
