import inspect

import pytest

import sentry_sdk


def my_example_function():
    return "return_of_sync_function"


async def my_async_example_function():
    return "return_of_async_function"


def test_trace_decorator(sentry_init, capture_items):
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
        span["name"] == "test_decorator.test_trace_decorator.<locals>.traced_function"
    )
    assert span["attributes"]["sentry.op"] == "function"
    assert span["status"] == "ok"


def test_trace_decorator_arguments(sentry_init, capture_items):
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


def test_trace_decorator_inactive(sentry_init, capture_items):
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
async def test_trace_decorator_async(sentry_init, capture_items):
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
        == "test_decorator.test_trace_decorator_async.<locals>.traced_function"
    )
    assert span["attributes"]["sentry.op"] == "function"
    assert span["status"] == "ok"


@pytest.mark.asyncio
async def test_trace_decorator_async_arguments(sentry_init, capture_items):
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
async def test_trace_decorator_async_inactive(sentry_init, capture_items):
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


def test_trace_decorator_child(sentry_init, capture_items):
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
        == "test_decorator.test_trace_decorator_child.<locals>._some_function_traced_stream"
    )
    assert child_span["parent_span_id"] == segment.span_id
    assert segment_span["name"] == "segment"
    assert "parent_span_id" not in segment_span


@pytest.mark.asyncio
async def test_trace_decorator_async_child(sentry_init, capture_items):
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
        == "test_decorator.test_trace_decorator_async_child.<locals>._some_function_traced_stream"
    )
    assert child_span["parent_span_id"] == segment.span_id
    assert segment_span["name"] == "segment"
    assert "parent_span_id" not in segment_span


def test_functions_to_trace_signature_unchanged_sync(sentry_init):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    def _some_function(a, b, c):
        pass

    @sentry_sdk.traces.trace
    def _some_function_traced_stream(a, b, c):
        pass

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

    @sentry_sdk.traces.trace
    async def _some_function_traced_stream(a, b, c):
        pass

    assert inspect.getcallargs(_some_function, 1, 2, 3) == inspect.getcallargs(
        _some_function_traced_stream, 1, 2, 3
    )
