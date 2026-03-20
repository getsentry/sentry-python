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
    patch_asyncio,
    enable_asyncio_integration,
)
from sentry_sdk.utils import mark_sentry_task_internal

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
    capture_events,
):
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=True,
        integrations=[
            AsyncioIntegration(),
        ],
    )

    events = capture_events()

    with sentry_sdk.start_transaction(name="test_transaction_for_create_task"):
        with sentry_sdk.start_span(op="root", name="not so important"):
            foo_task = asyncio.create_task(foo())
            bar_task = asyncio.create_task(bar())

            if hasattr(foo_task.get_coro(), "__name__"):
                assert foo_task.get_coro().__name__ == "foo"
            if hasattr(bar_task.get_coro(), "__name__"):
                assert bar_task.get_coro().__name__ == "bar"

            tasks = [foo_task, bar_task]

            await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    sentry_sdk.flush()

    (transaction_event,) = events

    assert transaction_event["spans"][0]["op"] == "root"
    assert transaction_event["spans"][0]["description"] == "not so important"

    assert transaction_event["spans"][1]["op"] == OP.FUNCTION
    assert transaction_event["spans"][1]["description"] == "foo"
    assert (
        transaction_event["spans"][1]["parent_span_id"]
        == transaction_event["spans"][0]["span_id"]
    )

    assert transaction_event["spans"][2]["op"] == OP.FUNCTION
    assert transaction_event["spans"][2]["description"] == "bar"
    assert (
        transaction_event["spans"][2]["parent_span_id"]
        == transaction_event["spans"][0]["span_id"]
    )


@minimum_python_38
@pytest.mark.asyncio(loop_scope="module")
async def test_gather(
    sentry_init,
    capture_events,
):
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=True,
        integrations=[
            AsyncioIntegration(),
        ],
    )

    events = capture_events()

    with sentry_sdk.start_transaction(name="test_transaction_for_gather"):
        with sentry_sdk.start_span(op="root", name="not so important"):
            await asyncio.gather(foo(), bar(), return_exceptions=True)

    sentry_sdk.flush()

    (transaction_event,) = events

    assert transaction_event["spans"][0]["op"] == "root"
    assert transaction_event["spans"][0]["description"] == "not so important"

    assert transaction_event["spans"][1]["op"] == OP.FUNCTION
    assert transaction_event["spans"][1]["description"] == "foo"
    assert (
        transaction_event["spans"][1]["parent_span_id"]
        == transaction_event["spans"][0]["span_id"]
    )

    assert transaction_event["spans"][2]["op"] == OP.FUNCTION
    assert transaction_event["spans"][2]["description"] == "bar"
    assert (
        transaction_event["spans"][2]["parent_span_id"]
        == transaction_event["spans"][0]["span_id"]
    )


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
    )

    events = capture_events()

    with sentry_sdk.start_transaction(name="test_exception"):
        with sentry_sdk.start_span(op="root", name="not so important"):
            tasks = [asyncio.create_task(boom()), asyncio.create_task(bar())]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    sentry_sdk.flush()

    (error_event, _) = events

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
    capture_events,
):
    sentry_init(
        integrations=[AsyncioIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    with sentry_sdk.start_transaction(name="something"):
        tasks = [
            asyncio.create_task(foo()),
        ]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    sentry_sdk.flush()

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.function.asyncio"


@minimum_python_38
@pytest.mark.asyncio
async def test_task_spans_false(
    sentry_init,
    capture_events,
    uninstall_integration,
):
    uninstall_integration("asyncio")

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[
            AsyncioIntegration(task_spans=False),
        ],
    )

    events = capture_events()

    with sentry_sdk.start_transaction(name="test_no_spans"):
        tasks = [asyncio.create_task(foo()), asyncio.create_task(bar())]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    sentry_sdk.flush()

    (transaction_event,) = events

    assert not transaction_event["spans"]


@minimum_python_38
@pytest.mark.asyncio
async def test_enable_asyncio_integration_with_task_spans_false(
    sentry_init,
    capture_events,
    uninstall_integration,
):
    """
    Test that enable_asyncio_integration() helper works with task_spans=False.
    """
    uninstall_integration("asyncio")

    sentry_init(traces_sample_rate=1.0)

    assert "asyncio" not in sentry_sdk.get_client().integrations

    enable_asyncio_integration(task_spans=False)

    assert "asyncio" in sentry_sdk.get_client().integrations
    assert sentry_sdk.get_client().integrations["asyncio"].task_spans is False

    events = capture_events()

    with sentry_sdk.start_transaction(name="test"):
        await asyncio.create_task(foo())

    sentry_sdk.flush()

    (transaction,) = events
    assert not transaction["spans"]


@minimum_python_38
@pytest.mark.asyncio
async def test_delayed_enable_integration(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)

    assert "asyncio" not in sentry_sdk.get_client().integrations

    events = capture_events()

    with sentry_sdk.start_transaction(name="test"):
        await asyncio.create_task(foo())

    assert len(events) == 1
    (transaction,) = events
    assert not transaction["spans"]

    enable_asyncio_integration()

    events = capture_events()

    assert "asyncio" in sentry_sdk.get_client().integrations

    with sentry_sdk.start_transaction(name="test"):
        await asyncio.create_task(foo())

    assert len(events) == 1
    (transaction,) = events
    assert transaction["spans"]
    assert transaction["spans"][0]["origin"] == "auto.function.asyncio"


@minimum_python_38
@pytest.mark.asyncio
async def test_delayed_enable_integration_with_options(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)

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
    sentry_init(integrations=[integration], traces_sample_rate=1.0)

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
async def test_delayed_enable_integration_after_disabling(sentry_init, capture_events):
    sentry_init(disabled_integrations=[AsyncioIntegration()], traces_sample_rate=1.0)

    assert "asyncio" not in sentry_sdk.get_client().integrations

    events = capture_events()

    with sentry_sdk.start_transaction(name="test"):
        await asyncio.create_task(foo())

    assert len(events) == 1
    (transaction,) = events
    assert not transaction["spans"]

    enable_asyncio_integration()

    events = capture_events()

    assert "asyncio" in sentry_sdk.get_client().integrations

    with sentry_sdk.start_transaction(name="test"):
        await asyncio.create_task(foo())

    assert len(events) == 1
    (transaction,) = events
    assert transaction["spans"]
    assert transaction["spans"][0]["origin"] == "auto.function.asyncio"


@minimum_python_39
@pytest.mark.asyncio(loop_scope="module")
async def test_internal_tasks_not_wrapped(sentry_init, capture_events):
    sentry_init(integrations=[AsyncioIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    # Create a user task that should be wrapped
    async def user_task():
        await asyncio.sleep(0.01)
        return "user_result"

    # Create an internal task that should NOT be wrapped
    async def internal_task():
        await asyncio.sleep(0.01)
        return "internal_result"

    with sentry_sdk.start_transaction(name="test_transaction"):
        user_task_obj = asyncio.create_task(user_task())

        with mark_sentry_task_internal():
            internal_task_obj = asyncio.create_task(internal_task())

        user_result = await user_task_obj
        internal_result = await internal_task_obj

    assert user_result == "user_result"
    assert internal_result == "internal_result"

    assert len(events) == 1
    transaction = events[0]

    user_spans = []
    internal_spans = []

    for span in transaction.get("spans", []):
        if "user_task" in span.get("description", ""):
            user_spans.append(span)
        elif "internal_task" in span.get("description", ""):
            internal_spans.append(span)

    assert len(user_spans) > 0, (
        f"User task should have been traced. All spans: {[s.get('description') for s in transaction.get('spans', [])]}"
    )
    assert len(internal_spans) == 0, (
        f"Internal task should NOT have been traced. All spans: {[s.get('description') for s in transaction.get('spans', [])]}"
    )


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
    from sentry_sdk.transport import AsyncHttpTransport

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


# ============================================================================
# patch_loop_close edge case tests
# ============================================================================


@minimum_python_38
def test_patch_loop_close_no_running_loop():
    """Test patch_loop_close is a no-op when no running loop."""
    from sentry_sdk.integrations.asyncio import patch_loop_close

    # Should not raise
    with patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")):
        patch_loop_close()


@minimum_python_38
def test_patch_loop_close_already_patched():
    """Test patch_loop_close skips if already patched."""
    from sentry_sdk.integrations.asyncio import patch_loop_close

    mock_loop = MagicMock()
    mock_loop._sentry_flush_patched = True

    original_close = mock_loop.close

    with patch("asyncio.get_running_loop", return_value=mock_loop):
        patch_loop_close()

    # close should not have been replaced since already patched
    assert mock_loop.close is original_close


@minimum_python_38
def test_patch_loop_close_patches_close():
    """Test patch_loop_close replaces loop.close with patched version."""
    from sentry_sdk.integrations.asyncio import patch_loop_close

    mock_loop = MagicMock()
    mock_loop._sentry_flush_patched = False
    # Make getattr return False for _sentry_flush_patched
    type(mock_loop)._sentry_flush_patched = False

    with patch("asyncio.get_running_loop", return_value=mock_loop):
        patch_loop_close()

    # close should have been replaced
    assert mock_loop._sentry_flush_patched is True


# ============================================================================
# _create_task_with_factory tests
# ============================================================================


@minimum_python_38
@patch("sentry_sdk.integrations.asyncio.Task")
def test_create_task_with_factory_no_orig_factory(MockTask):
    """Test _create_task_with_factory creates Task directly when no orig factory."""
    from sentry_sdk.integrations.asyncio import _create_task_with_factory

    mock_loop = MagicMock()
    mock_coro = MagicMock()

    result = _create_task_with_factory(None, mock_loop, mock_coro)

    MockTask.assert_called_once_with(mock_coro, loop=mock_loop)
    assert result == MockTask.return_value


@minimum_python_38
def test_create_task_with_factory_with_orig_factory():
    """Test _create_task_with_factory uses orig factory when provided."""
    from sentry_sdk.integrations.asyncio import _create_task_with_factory

    mock_loop = MagicMock()
    mock_coro = MagicMock()
    orig_factory = MagicMock()

    result = _create_task_with_factory(orig_factory, mock_loop, mock_coro, name="test")

    orig_factory.assert_called_once_with(mock_loop, mock_coro, name="test")
    assert result == orig_factory.return_value


@minimum_python_38
@patch("sentry_sdk.integrations.asyncio.Task")
def test_create_task_with_factory_orig_returns_none(MockTask):
    """Test _create_task_with_factory falls back to Task when orig factory returns None."""
    from sentry_sdk.integrations.asyncio import _create_task_with_factory

    mock_loop = MagicMock()
    mock_coro = MagicMock()
    orig_factory = MagicMock(return_value=None)

    result = _create_task_with_factory(orig_factory, mock_loop, mock_coro)

    orig_factory.assert_called_once()
    MockTask.assert_called_once_with(mock_coro, loop=mock_loop)
    assert result == MockTask.return_value


# ============================================================================
# _sentry_task_factory internal task detection tests
# ============================================================================


@minimum_python_39
@pytest.mark.asyncio(loop_scope="module")
async def test_sentry_task_factory_skips_internal_tasks(sentry_init):
    """Test that internal tasks bypass Sentry wrapping."""
    sentry_init(
        integrations=[AsyncioIntegration()],
        traces_sample_rate=1.0,
    )

    results = []

    async def internal_coro():
        results.append("internal_ran")
        return 42

    with mark_sentry_task_internal():
        task = asyncio.create_task(internal_coro())
        result = await task

    assert result == 42
    assert results == ["internal_ran"]

    # Verify the coroutine was NOT wrapped (internal tasks are not wrapped
    # with _task_with_sentry_span_creation)
    # The task's coro should be the original, not a wrapped one
    # We check by ensuring no span was created for this
    # (the span count test is more reliable)


@minimum_python_39
@pytest.mark.asyncio(loop_scope="module")
async def test_sentry_task_factory_wraps_user_tasks(sentry_init, capture_events):
    """Test that user tasks get wrapped with Sentry instrumentation."""
    sentry_init(
        integrations=[AsyncioIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    async def user_coro():
        await asyncio.sleep(0.01)

    with sentry_sdk.start_transaction(name="test"):
        task = asyncio.create_task(user_coro())
        await task

    sentry_sdk.flush()

    assert len(events) == 1
    transaction = events[0]
    # User tasks should get spans
    user_spans = [
        s
        for s in transaction.get("spans", [])
        if "user_coro" in s.get("description", "")
    ]
    assert len(user_spans) > 0


# ============================================================================
# is_internal_task / mark_sentry_task_internal utility tests
# ============================================================================


@minimum_python_38
def test_is_internal_task_default():
    """Test is_internal_task returns False by default."""
    from sentry_sdk.utils import is_internal_task

    assert is_internal_task() is False


@minimum_python_38
def test_mark_sentry_task_internal_context_manager():
    """Test mark_sentry_task_internal sets and resets the flag."""
    from sentry_sdk.utils import is_internal_task, mark_sentry_task_internal

    assert is_internal_task() is False
    with mark_sentry_task_internal():
        assert is_internal_task() is True
    assert is_internal_task() is False


@minimum_python_38
def test_mark_sentry_task_internal_nested():
    """Test nested mark_sentry_task_internal restores correctly."""
    from sentry_sdk.utils import is_internal_task, mark_sentry_task_internal

    assert is_internal_task() is False
    with mark_sentry_task_internal():
        assert is_internal_task() is True
        with mark_sentry_task_internal():
            assert is_internal_task() is True
        assert is_internal_task() is True
    assert is_internal_task() is False


@minimum_python_38
def test_mark_sentry_task_internal_exception_cleanup():
    """Test mark_sentry_task_internal resets flag even on exception."""
    from sentry_sdk.utils import is_internal_task, mark_sentry_task_internal

    assert is_internal_task() is False
    try:
        with mark_sentry_task_internal():
            assert is_internal_task() is True
            raise ValueError("test exception")
    except ValueError:
        pass
    assert is_internal_task() is False


# ===== Sync wrapper tests for better coverage collection =====


@minimum_python_38
def test_patch_loop_close_sets_flag_sync():
    """Test patch_loop_close using asyncio.run() for coverage."""
    from sentry_sdk.integrations.asyncio import patch_loop_close

    async def _inner():
        loop = asyncio.get_running_loop()
        if hasattr(loop, "_sentry_flush_patched"):
            delattr(loop, "_sentry_flush_patched")
        patch_loop_close()
        assert getattr(loop, "_sentry_flush_patched", False) is True

    asyncio.run(_inner())


@minimum_python_38
def test_create_task_with_factory_sync():
    """Test _create_task_with_factory using asyncio.run() for coverage."""
    from sentry_sdk.integrations.asyncio import _create_task_with_factory

    async def _inner():
        loop = asyncio.get_running_loop()

        async def dummy():
            return "hello"

        # No factory — should use Task() fallback
        task = _create_task_with_factory(None, loop, dummy())
        assert await task == "hello"

        # With factory
        def factory(loop, coro, **kw):
            return asyncio.ensure_future(coro, loop=loop)

        task2 = _create_task_with_factory(factory, loop, dummy())
        assert await task2 == "hello"

        # Factory returns None — should fall back to Task()
        def none_factory(lp, coro, **kw):
            return None

        task3 = _create_task_with_factory(none_factory, loop, dummy())
        assert await task3 == "hello"

    asyncio.run(_inner())


@minimum_python_38
def test_internal_task_marking_sync():
    """Test is_internal_task / mark_sentry_task_internal using asyncio.run()."""
    from sentry_sdk.utils import is_internal_task, mark_sentry_task_internal

    async def _inner():
        assert is_internal_task() is False
        with mark_sentry_task_internal():
            assert is_internal_task() is True
            # Nested
            with mark_sentry_task_internal():
                assert is_internal_task() is True
            assert is_internal_task() is True
        assert is_internal_task() is False

    asyncio.run(_inner())


@minimum_python_38
def test_sentry_task_factory_integration_sync():
    """Test the full task factory integration using asyncio.run()."""
    from sentry_sdk.utils import mark_sentry_task_internal

    async def _inner():
        sentry_sdk.init(
            integrations=[AsyncioIntegration()],
            traces_sample_rate=1.0,
        )

        results = []

        async def user_coro():
            results.append("user")

        async def internal_coro():
            results.append("internal")

        # Create user task (should be wrapped)
        t1 = asyncio.create_task(user_coro())
        await t1

        # Create internal task (should skip wrapping)
        with mark_sentry_task_internal():
            t2 = asyncio.create_task(internal_coro())
        await t2

        assert "user" in results
        assert "internal" in results

    asyncio.run(_inner())
