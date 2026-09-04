import asyncio
import inspect
import sys
from unittest.mock import MagicMock, Mock, patch

if sys.version_info >= (3, 8):
    from unittest.mock import AsyncMock

import pytest

import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.integrations.asyncio import (
    AsyncioIntegration,
    enable_asyncio_integration,
    patch_asyncio,
)

try:
    from contextvars import Context, ContextVar
except ImportError:
    pass  # All tests will be skipped with incompatible versions


minimum_python_38 = pytest.mark.skipif(
    sys.version_info < (3, 8), reason="Asyncio tests need Python >= 3.8"
)


minimum_python_39 = pytest.mark.skipif(
    sys.version_info < (3, 9), reason="Test requires Python >= 3.9"
)


minimum_python_311 = pytest.mark.skipif(
    sys.version_info < (3, 11),
    reason="Asyncio task context parameter was introduced in Python 3.11",
)


async def foo():
    await asyncio.sleep(0.01)


async def bar():
    await asyncio.sleep(0.01)


async def boom():
    1 / 0


def get_sentry_task_factory(mock_get_running_loop):
    """
    Patches (mocked) asyncio and gets the sentry_task_factory.
    """
    mock_loop = mock_get_running_loop.return_value
    patch_asyncio()
    patched_factory = mock_loop.set_task_factory.call_args[0][0]

    return patched_factory


@minimum_python_38
@pytest.mark.asyncio(loop_scope="module")
async def test_create_task(
    sentry_init,
    capture_items,
):
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=True,
        integrations=[
            AsyncioIntegration(),
        ],
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    with sentry_sdk.traces.start_span(
        name="not so important", attributes={"sentry.op": "root"}
    ):
        foo_task = asyncio.create_task(foo())
        bar_task = asyncio.create_task(bar())

        if hasattr(foo_task.get_coro(), "__name__"):
            assert foo_task.get_coro().__name__ == "foo"
        if hasattr(bar_task.get_coro(), "__name__"):
            assert bar_task.get_coro().__name__ == "bar"

        tasks = [foo_task, bar_task]

        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    sentry_sdk.flush()

    segment = items.pop().payload

    assert segment["is_segment"] is True
    assert segment["name"] == "not so important"
    assert segment["attributes"]["sentry.op"] == "root"

    spans = [item.payload for item in items]
    assert len(spans) == 2

    assert spans[0]["attributes"]["sentry.op"] == OP.FUNCTION
    assert spans[0]["name"] == "foo"
    assert spans[0]["parent_span_id"] == segment["span_id"]

    assert spans[1]["attributes"]["sentry.op"] == OP.FUNCTION
    assert spans[1]["name"] == "bar"
    assert spans[1]["parent_span_id"] == segment["span_id"]


@minimum_python_38
@pytest.mark.asyncio(loop_scope="module")
async def test_gather(
    sentry_init,
    capture_items,
):
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=True,
        integrations=[
            AsyncioIntegration(),
        ],
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    with sentry_sdk.traces.start_span(
        name="not so important", attributes={"sentry.op": "root"}
    ):
        await asyncio.gather(foo(), bar(), return_exceptions=True)

    sentry_sdk.flush()

    segment = items.pop().payload
    assert segment["is_segment"] is True
    assert segment["name"] == "not so important"
    assert segment["attributes"]["sentry.op"] == "root"

    spans = [item.payload for item in items]
    assert len(spans) == 2

    assert spans[0]["attributes"]["sentry.op"] == OP.FUNCTION
    assert spans[0]["name"] == "foo"
    assert spans[0]["parent_span_id"] == segment["span_id"]

    assert spans[1]["attributes"]["sentry.op"] == OP.FUNCTION
    assert spans[1]["name"] == "bar"
    assert spans[1]["parent_span_id"] == segment["span_id"]


@minimum_python_38
@pytest.mark.asyncio(loop_scope="module")
async def test_exception(
    sentry_init,
    capture_events,
):
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=True,
        integrations=[
            AsyncioIntegration(),
        ],
        trace_lifecycle="stream",
    )

    events = capture_events()

    with sentry_sdk.traces.start_span(name="test_exception", parent_span=None):
        with sentry_sdk.traces.start_span(name="not so important"):
            tasks = [asyncio.create_task(boom()), asyncio.create_task(bar())]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    sentry_sdk.flush()

    (error_event,) = events

    assert error_event["transaction"] == "test_exception"
    assert error_event["contexts"]["trace"]["op"] == "function"
    assert error_event["exception"]["values"][0]["type"] == "ZeroDivisionError"
    assert error_event["exception"]["values"][0]["value"] == "division by zero"
    assert error_event["exception"]["values"][0]["mechanism"]["handled"] is False
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "asyncio"


@minimum_python_38
@pytest.mark.asyncio(loop_scope="module")
async def test_task_result(sentry_init):
    sentry_init(
        integrations=[
            AsyncioIntegration(),
        ],
    )

    async def add(a, b):
        return a + b

    result = await asyncio.create_task(add(1, 2))
    assert result == 3, result


@minimum_python_311
@pytest.mark.asyncio(loop_scope="module")
async def test_task_with_context(sentry_init):
    """
    Integration test to ensure working context parameter in Python 3.11+
    """
    sentry_init(
        integrations=[
            AsyncioIntegration(),
        ],
    )

    var = ContextVar("var")
    var.set("original value")

    async def change_value():
        var.set("changed value")

    async def retrieve_value():
        return var.get()

    # Create a context and run both tasks within the context
    ctx = Context()
    async with asyncio.TaskGroup() as tg:
        tg.create_task(change_value(), context=ctx)
        retrieve_task = tg.create_task(retrieve_value(), context=ctx)

    assert retrieve_task.result() == "changed value"


@minimum_python_38
@patch("asyncio.get_running_loop")
def test_patch_asyncio(mock_get_running_loop):
    """
    Test that the patch_asyncio function will patch the task factory.
    """
    mock_loop = mock_get_running_loop.return_value
    mock_loop.get_task_factory.return_value._is_sentry_task_factory = False

    patch_asyncio()

    assert mock_loop.set_task_factory.called

    set_task_factory_args, _ = mock_loop.set_task_factory.call_args
    assert len(set_task_factory_args) == 1

    sentry_task_factory, *_ = set_task_factory_args
    assert callable(sentry_task_factory)


@minimum_python_38
@patch("asyncio.get_running_loop")
@patch("sentry_sdk.integrations.asyncio.Task")
def test_sentry_task_factory_no_factory(MockTask, mock_get_running_loop):  # noqa: N803
    mock_loop = mock_get_running_loop.return_value
    mock_coro = MagicMock()

    # Set the original task factory to None
    mock_loop.get_task_factory.return_value = None

    # Retieve sentry task factory (since it is an inner function within patch_asyncio)
    sentry_task_factory = get_sentry_task_factory(mock_get_running_loop)

    # The call we are testing
    ret_val = sentry_task_factory(mock_loop, mock_coro)

    assert MockTask.called
    assert ret_val == MockTask.return_value

    task_args, task_kwargs = MockTask.call_args
    assert len(task_args) == 1

    coro_param, *_ = task_args
    assert inspect.iscoroutine(coro_param)

    assert "loop" in task_kwargs
    assert task_kwargs["loop"] == mock_loop


@minimum_python_38
@patch("asyncio.get_running_loop")
def test_sentry_task_factory_with_factory(mock_get_running_loop):
    mock_loop = mock_get_running_loop.return_value
    mock_coro = MagicMock()

    # The original task factory will be mocked out here, let's retrieve the value for later
    orig_task_factory = mock_loop.get_task_factory.return_value
    orig_task_factory._is_sentry_task_factory = False

    # Retieve sentry task factory (since it is an inner function within patch_asyncio)
    sentry_task_factory = get_sentry_task_factory(mock_get_running_loop)

    # The call we are testing
    ret_val = sentry_task_factory(mock_loop, mock_coro)

    assert orig_task_factory.called
    assert ret_val == orig_task_factory.return_value

    task_factory_args, _ = orig_task_factory.call_args
    assert len(task_factory_args) == 2

    loop_arg, coro_arg = task_factory_args
    assert loop_arg == mock_loop
    assert inspect.iscoroutine(coro_arg)


@minimum_python_311
@patch("asyncio.get_running_loop")
@patch("sentry_sdk.integrations.asyncio.Task")
def test_sentry_task_factory_context_no_factory(
    MockTask,
    mock_get_running_loop,  # noqa: N803
):
    mock_loop = mock_get_running_loop.return_value
    mock_coro = MagicMock()
    mock_context = MagicMock()

    # Set the original task factory to None
    mock_loop.get_task_factory.return_value = None

    # Retieve sentry task factory (since it is an inner function within patch_asyncio)
    sentry_task_factory = get_sentry_task_factory(mock_get_running_loop)

    # The call we are testing
    ret_val = sentry_task_factory(mock_loop, mock_coro, context=mock_context)

    assert MockTask.called
    assert ret_val == MockTask.return_value

    task_args, task_kwargs = MockTask.call_args
    assert len(task_args) == 1

    coro_param, *_ = task_args
    assert inspect.iscoroutine(coro_param)

    assert "loop" in task_kwargs
    assert task_kwargs["loop"] == mock_loop
    assert "context" in task_kwargs
    assert task_kwargs["context"] == mock_context


@minimum_python_311
@patch("asyncio.get_running_loop")
def test_sentry_task_factory_context_with_factory(mock_get_running_loop):
    mock_loop = mock_get_running_loop.return_value
    mock_coro = MagicMock()
    mock_context = MagicMock()

    # The original task factory will be mocked out here, let's retrieve the value for later
    orig_task_factory = mock_loop.get_task_factory.return_value
    orig_task_factory._is_sentry_task_factory = False

    # Retieve sentry task factory (since it is an inner function within patch_asyncio)
    sentry_task_factory = get_sentry_task_factory(mock_get_running_loop)

    # The call we are testing
    ret_val = sentry_task_factory(mock_loop, mock_coro, context=mock_context)

    assert orig_task_factory.called
    assert ret_val == orig_task_factory.return_value

    task_factory_args, task_factory_kwargs = orig_task_factory.call_args
    assert len(task_factory_args) == 2

    loop_arg, coro_arg = task_factory_args
    assert loop_arg == mock_loop
    assert inspect.iscoroutine(coro_arg)

    assert "context" in task_factory_kwargs
    assert task_factory_kwargs["context"] == mock_context


@minimum_python_38
@pytest.mark.asyncio(loop_scope="module")
async def test_span_origin(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[AsyncioIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="something"):
        tasks = [
            asyncio.create_task(foo()),
        ]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    sentry_sdk.flush()

    segment = items.pop().payload
    assert segment["is_segment"] is True
    assert segment["attributes"]["sentry.origin"] == "manual"

    spans = [item.payload for item in items]
    assert len(spans) == 1
    assert spans[0]["attributes"]["sentry.origin"] == "auto.function.asyncio"


@minimum_python_38
@pytest.mark.asyncio
async def test_task_spans_false(
    sentry_init,
    capture_items,
    uninstall_integration,
):
    uninstall_integration("asyncio")

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[
            AsyncioIntegration(task_spans=False),
        ],
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="test_no_spans"):
        tasks = [asyncio.create_task(foo()), asyncio.create_task(bar())]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    sentry_sdk.flush()

    segment = items.pop().payload
    assert segment["is_segment"] is True
    assert segment["name"] == "test_no_spans"

    spans = [item.payload for item in items]
    assert len(spans) == 0


@minimum_python_38
@pytest.mark.asyncio
async def test_enable_asyncio_integration_with_task_spans_false(
    sentry_init,
    capture_items,
    uninstall_integration,
):
    """
    Test that enable_asyncio_integration() helper works with task_spans=False.
    """
    uninstall_integration("asyncio")

    sentry_init(traces_sample_rate=1.0, trace_lifecycle="stream")

    assert "asyncio" not in sentry_sdk.get_client().integrations

    enable_asyncio_integration(task_spans=False)

    assert "asyncio" in sentry_sdk.get_client().integrations
    assert sentry_sdk.get_client().integrations["asyncio"].task_spans is False

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="test"):
        await asyncio.create_task(foo())

    sentry_sdk.flush()

    segment = items.pop().payload
    assert segment["is_segment"] is True

    spans = [item.payload for item in items]
    assert len(spans) == 0


@minimum_python_38
@pytest.mark.asyncio
async def test_delayed_enable_integration(
    sentry_init,
    capture_items,
):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    assert "asyncio" not in sentry_sdk.get_client().integrations

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="test"):
        await asyncio.create_task(foo())

    sentry_sdk.flush()

    assert len(items) == 1
    assert items[0].payload.get("is_segment") is True

    items.clear()

    enable_asyncio_integration()

    assert "asyncio" in sentry_sdk.get_client().integrations

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="test"):
        await asyncio.create_task(foo())

    sentry_sdk.flush()

    segment = items.pop().payload
    assert segment["is_segment"] is True

    spans = [item.payload for item in items]
    assert len(spans) == 1
    assert spans[0]["attributes"]["sentry.origin"] == "auto.function.asyncio"


@minimum_python_38
@pytest.mark.asyncio
async def test_delayed_enable_integration_with_options(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, trace_lifecycle="stream")

    assert "asyncio" not in sentry_sdk.get_client().integrations

    mock_init = MagicMock(return_value=None)
    mock_setup_once = MagicMock()
    with patch(
        "sentry_sdk.integrations.asyncio.AsyncioIntegration.__init__", mock_init
    ):
        with patch(
            "sentry_sdk.integrations.asyncio.AsyncioIntegration.setup_once",
            mock_setup_once,
        ):
            enable_asyncio_integration("arg", kwarg="kwarg")

    assert "asyncio" in sentry_sdk.get_client().integrations
    mock_init.assert_called_once_with("arg", kwarg="kwarg")
    mock_setup_once.assert_called_once()


@minimum_python_38
@pytest.mark.asyncio
async def test_delayed_enable_enabled_integration(sentry_init, uninstall_integration):
    # Ensure asyncio integration is not already installed from previous tests
    uninstall_integration("asyncio")

    integration = AsyncioIntegration()
    sentry_init(
        integrations=[integration], traces_sample_rate=1.0, trace_lifecycle="stream"
    )

    assert "asyncio" in sentry_sdk.get_client().integrations

    # Get the task factory after initial setup - it should be Sentry's
    loop = asyncio.get_running_loop()
    task_factory_before = loop.get_task_factory()
    assert getattr(task_factory_before, "_is_sentry_task_factory", False) is True

    enable_asyncio_integration()

    assert "asyncio" in sentry_sdk.get_client().integrations

    # The task factory should be the same (loop not re-patched)
    task_factory_after = loop.get_task_factory()
    assert task_factory_before is task_factory_after


@minimum_python_38
@pytest.mark.asyncio
async def test_delayed_enable_integration_after_disabling(
    sentry_init,
    capture_items,
):
    sentry_init(
        disabled_integrations=[AsyncioIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    assert "asyncio" not in sentry_sdk.get_client().integrations

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="test"):
        await asyncio.create_task(foo())

    sentry_sdk.flush()

    assert len(items) == 1
    assert items[0].payload.get("is_segment") is True

    items.clear()

    enable_asyncio_integration()

    assert "asyncio" in sentry_sdk.get_client().integrations

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="test"):
        await asyncio.create_task(foo())

    sentry_sdk.flush()

    segment = items.pop().payload
    assert segment["is_segment"] is True

    spans = [item.payload for item in items]
    assert len(spans) == 1
    assert spans[0]["attributes"]["sentry.origin"] == "auto.function.asyncio"


@minimum_python_39
@pytest.mark.asyncio(loop_scope="module")
async def test_internal_tasks_not_wrapped(
    sentry_init,
    capture_items,
):
    from sentry_sdk.utils import mark_sentry_task_internal

    sentry_init(
        integrations=[AsyncioIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    async def user_task():
        await asyncio.sleep(0.01)
        return "user_result"

    async def internal_task():
        await asyncio.sleep(0.01)
        return "internal_result"

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="test_streamed_span"):
        user_task_obj = asyncio.create_task(user_task())

        with mark_sentry_task_internal():
            internal_task_obj = asyncio.create_task(internal_task())

        user_result = await user_task_obj
        internal_result = await internal_task_obj

    assert user_result == "user_result"
    assert internal_result == "internal_result"

    sentry_sdk.flush()

    assert len(items) == 2

    segment = items.pop().payload
    assert segment["is_segment"] is True
    assert segment["name"] == "test_streamed_span"

    spans = [item.payload for item in items]
    assert len(spans) == 1
    assert spans[0]["name"].endswith("user_task")


@minimum_python_38
def test_loop_close_patching(sentry_init):
    sentry_init(integrations=[AsyncioIntegration()])

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        with patch("asyncio.get_running_loop", return_value=loop):
            assert not hasattr(loop, "_sentry_flush_patched")
            AsyncioIntegration.setup_once()
            assert hasattr(loop, "_sentry_flush_patched")
            assert loop._sentry_flush_patched is True

    finally:
        if not loop.is_closed():
            loop.close()


@minimum_python_38
def test_loop_close_flushes_async_transport(sentry_init):
    from sentry_sdk.transport import ASYNC_TRANSPORT_AVAILABLE, AsyncHttpTransport

    if not ASYNC_TRANSPORT_AVAILABLE:
        pytest.skip("httpcore[asyncio] not installed")

    sentry_init(integrations=[AsyncioIntegration()])

    # Save the current event loop to restore it later
    try:
        original_loop = asyncio.get_event_loop()
    except RuntimeError:
        original_loop = None

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        with patch("asyncio.get_running_loop", return_value=loop):
            AsyncioIntegration.setup_once()

        mock_client = Mock()
        mock_transport = Mock(spec=AsyncHttpTransport)
        mock_client.transport = mock_transport
        mock_client.close_async = AsyncMock(return_value=None)

        with patch("sentry_sdk.get_client", return_value=mock_client):
            loop.close()

        mock_client.close_async.assert_called_once()
        mock_client.close_async.assert_awaited_once()

    finally:
        if not loop.is_closed():
            loop.close()
        if original_loop:
            asyncio.set_event_loop(original_loop)
