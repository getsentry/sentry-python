import asyncio
import inspect
import sys
from unittest.mock import MagicMock, patch

import pytest

import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.integrations.asyncio import AsyncioIntegration, patch_asyncio

try:
    from contextvars import Context, ContextVar
except ImportError:
    pass  # All tests will be skipped with incompatible versions


minimum_python_38 = pytest.mark.skipif(
    sys.version_info < (3, 8), reason="Asyncio tests need Python >= 3.8"
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
