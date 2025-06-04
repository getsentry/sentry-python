import inspect
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk.tracing import trace
from sentry_sdk.utils import logger


def my_example_function():
    return "return_of_sync_function"


async def my_async_example_function():
    return "return_of_async_function"


def test_trace_decorator(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with sentry_sdk.start_span(name="test"):
        result = my_example_function()
        assert result == "return_of_sync_function"

        result2 = trace(my_example_function)()
        assert result2 == "return_of_sync_function"

    (event,) = events
    (span,) = event["spans"]
    assert span["op"] == "function"
    assert span["description"] == "test_decorator.my_example_function"


def test_trace_decorator_no_trx(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with mock.patch.object(logger, "debug", mock.Mock()) as fake_debug:
        result = my_example_function()
        assert result == "return_of_sync_function"
        fake_debug.assert_not_called()

        result2 = trace(my_example_function)()
        assert result2 == "return_of_sync_function"
        fake_debug.assert_called_once_with(
            "Cannot create a child span for %s. "
            "Please start a Sentry transaction before calling this function.",
            "test_decorator.my_example_function",
        )

    assert len(events) == 0


@pytest.mark.asyncio
async def test_trace_decorator_async(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with sentry_sdk.start_span(name="test"):
        result = await my_async_example_function()
        assert result == "return_of_async_function"

        result2 = await trace(my_async_example_function)()
        assert result2 == "return_of_async_function"

    (event,) = events
    (span,) = event["spans"]
    assert span["op"] == "function"
    assert span["description"] == "test_decorator.my_async_example_function"


@pytest.mark.asyncio
async def test_trace_decorator_async_no_trx(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with mock.patch.object(logger, "debug", mock.Mock()) as fake_debug:
        result = await my_async_example_function()
        fake_debug.assert_not_called()
        assert result == "return_of_async_function"

        result2 = await trace(my_async_example_function)()
        fake_debug.assert_called_once_with(
            "Cannot create a child span for %s. "
            "Please start a Sentry transaction before calling this function.",
            "test_decorator.my_async_example_function",
        )
        assert result2 == "return_of_async_function"

    assert len(events) == 0


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
