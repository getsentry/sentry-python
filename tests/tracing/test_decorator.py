import inspect
from unittest import mock

import pytest

import sentry_sdk
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

        start_child_span_decorator = create_span_decorator()
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

            start_child_span_decorator = create_span_decorator()
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

        start_child_span_decorator = create_span_decorator()
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

            start_child_span_decorator = create_span_decorator()
            result2 = await start_child_span_decorator(my_async_example_function)()
            fake_debug.assert_any_call(
                "Cannot create a child span for %s. "
                "Please start a Sentry transaction before calling this function.",
                "test_decorator.my_async_example_function",
            )
            assert result2 == "return_of_async_function"


def test_trace_decorator_span_streaming(sentry_init, capture_items):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    @sentry_sdk.traces.trace
    def traced_function():
        return "ok"

    result = traced_function()
    assert result == "ok"

    sentry_sdk.get_client().flush()
    spans = [item.payload for item in items]

    assert len(spans) == 1
    (span,) = spans

    assert (
        span["name"]
        == "test_decorator.test_trace_decorator_span_streaming.<locals>.traced_function"
    )
    assert span["attributes"]["sentry.op"] == "function"
    assert span["status"] == "ok"


def test_trace_decorator_arguments_span_streaming(sentry_init, capture_items):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    @sentry_sdk.traces.trace(name="traced", attributes={"traced.attribute": 123})
    def traced_function():
        return "ok"

    result = traced_function()
    assert result == "ok"

    sentry_sdk.get_client().flush()
    spans = [item.payload for item in items]

    assert len(spans) == 1
    (span,) = spans

    assert span["name"] == "traced"
    assert span["attributes"]["traced.attribute"] == 123
    assert span["attributes"]["sentry.op"] == "function"
    assert span["status"] == "ok"


def test_trace_decorator_inactive_span_streaming(sentry_init, capture_items):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    @sentry_sdk.traces.trace(name="outer", active=False)
    def traced_function():
        with sentry_sdk.traces.start_span(name="inner"):
            return "ok"

    result = traced_function()
    assert result == "ok"

    sentry_sdk.get_client().flush()
    spans = [item.payload for item in items]

    assert len(spans) == 2
    (span1, span2) = spans

    assert span1["name"] == "inner"
    assert span1.get("parent_span_id") != span2["span_id"]

    assert span2["name"] == "outer"


@pytest.mark.asyncio
async def test_trace_decorator_async_span_streaming(sentry_init, capture_items):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    @sentry_sdk.traces.trace
    async def traced_function():
        return "ok"

    result = await traced_function()
    assert result == "ok"

    sentry_sdk.get_client().flush()
    spans = [item.payload for item in items]

    assert len(spans) == 1
    (span,) = spans

    assert (
        span["name"]
        == "test_decorator.test_trace_decorator_async_span_streaming.<locals>.traced_function"
    )
    assert span["attributes"]["sentry.op"] == "function"
    assert span["status"] == "ok"


@pytest.mark.asyncio
async def test_trace_decorator_async_arguments_span_streaming(
    sentry_init, capture_items
):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    @sentry_sdk.traces.trace(name="traced", attributes={"traced.attribute": 123})
    async def traced_function():
        return "ok"

    result = await traced_function()
    assert result == "ok"

    sentry_sdk.get_client().flush()
    spans = [item.payload for item in items]

    assert len(spans) == 1
    (span,) = spans

    assert span["name"] == "traced"
    assert span["attributes"]["traced.attribute"] == 123
    assert span["attributes"]["sentry.op"] == "function"
    assert span["status"] == "ok"


@pytest.mark.asyncio
async def test_trace_decorator_async_inactive_span_streaming(
    sentry_init, capture_items
):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    @sentry_sdk.traces.trace(name="outer", active=False)
    async def traced_function():
        with sentry_sdk.traces.start_span(name="inner"):
            return "ok"

    result = await traced_function()
    assert result == "ok"

    sentry_sdk.get_client().flush()
    spans = [item.payload for item in items]

    assert len(spans) == 2
    (span1, span2) = spans

    assert span1["name"] == "inner"
    assert span1.get("parent_span_id") != span2["span_id"]

    assert span2["name"] == "outer"


def test_trace_decorator_child_span_streaming(sentry_init, capture_items):
    """Spans created with @trace show up as children if a span is active."""
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    @sentry_sdk.traces.trace
    def _some_function_traced_stream(a, b, c):
        return True

    with sentry_sdk.traces.start_span(name="segment") as segment:
        result = _some_function_traced_stream(1, 2, 3)

    assert result is True

    sentry_sdk.flush()

    assert len(items) == 2
    child_span, segment_span = items[0].payload, items[1].payload

    assert (
        child_span["name"]
        == "test_decorator.test_trace_decorator_child_span_streaming.<locals>._some_function_traced_stream"
    )
    assert child_span["parent_span_id"] == segment.span_id
    assert segment_span["name"] == "segment"
    assert "parent_span_id" not in segment_span


@pytest.mark.asyncio
async def test_trace_decorator_async_child_span_streaming(sentry_init, capture_items):
    """Spans created with @trace show up as children if a span is active."""
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    @sentry_sdk.traces.trace
    async def _some_function_traced_stream(a, b, c):
        return True

    with sentry_sdk.traces.start_span(name="segment") as segment:
        result = await _some_function_traced_stream(1, 2, 3)

    assert result is True

    sentry_sdk.flush()

    assert len(items) == 2
    child_span, segment_span = items[0].payload, items[1].payload

    assert (
        child_span["name"]
        == "test_decorator.test_trace_decorator_async_child_span_streaming.<locals>._some_function_traced_stream"
    )
    assert child_span["parent_span_id"] == segment.span_id
    assert segment_span["name"] == "segment"
    assert "parent_span_id" not in segment_span


def test_functions_to_trace_signature_unchanged_sync(sentry_init):
    sentry_init(
        traces_sample_rate=1.0,
    )

    def _some_function(a, b, c):
        pass

    @sentry_sdk.trace
    def _some_function_traced(a, b, c):
        pass

    @sentry_sdk.traces.trace
    def _some_function_traced_stream(a, b, c):
        pass

    assert inspect.getcallargs(_some_function, 1, 2, 3) == inspect.getcallargs(
        _some_function_traced, 1, 2, 3
    )

    assert inspect.getcallargs(_some_function, 1, 2, 3) == inspect.getcallargs(
        _some_function_traced_stream, 1, 2, 3
    )


@pytest.mark.asyncio
async def test_functions_to_trace_signature_unchanged_async(sentry_init):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    async def _some_function(a, b, c):
        pass

    @sentry_sdk.trace
    async def _some_function_traced(a, b, c):
        pass

    @sentry_sdk.traces.trace
    async def _some_function_traced_stream(a, b, c):
        pass

    assert inspect.getcallargs(_some_function, 1, 2, 3) == inspect.getcallargs(
        _some_function_traced, 1, 2, 3
    )
    assert inspect.getcallargs(_some_function, 1, 2, 3) == inspect.getcallargs(
        _some_function_traced_stream, 1, 2, 3
    )
