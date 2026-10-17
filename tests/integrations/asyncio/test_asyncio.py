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
from sentry_sdk.scope import Scope, ScopeType, _current_scope

try:
    from contextvars import Context, ContextVar, copy_context
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
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_create_task(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=True,
        integrations=[
            AsyncioIntegration(),
        ],
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming:
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

    else:
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

    if span_streaming:
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
    else:
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
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_gather(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=True,
        integrations=[
            AsyncioIntegration(),
        ],
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(
            name="not so important", attributes={"sentry.op": "root"}
        ):
            await asyncio.gather(foo(), bar(), return_exceptions=True)

    else:
        events = capture_events()

        with sentry_sdk.start_transaction(name="test_transaction_for_gather"):
            with sentry_sdk.start_span(op="root", name="not so important"):
                await asyncio.gather(foo(), bar(), return_exceptions=True)

    sentry_sdk.flush()

    if span_streaming:
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
    else:
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
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_span_origin(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[AsyncioIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="something"):
            tasks = [
                asyncio.create_task(foo()),
            ]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    else:
        events = capture_events()

        with sentry_sdk.start_transaction(name="something"):
            tasks = [
                asyncio.create_task(foo()),
            ]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    sentry_sdk.flush()

    if span_streaming:
        segment = items.pop().payload
        assert segment["is_segment"] is True
        assert segment["attributes"]["sentry.origin"] == "manual"

        spans = [item.payload for item in items]
        assert len(spans) == 1
        assert spans[0]["attributes"]["sentry.origin"] == "auto.function.asyncio"
    else:
        (event,) = events

        assert event["contexts"]["trace"]["origin"] == "manual"
        assert event["spans"][0]["origin"] == "auto.function.asyncio"


@minimum_python_38
@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_task_spans_false(
    sentry_init,
    capture_events,
    capture_items,
    uninstall_integration,
    span_streaming,
):
    uninstall_integration("asyncio")

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[
            AsyncioIntegration(task_spans=False),
        ],
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="test_no_spans"):
            tasks = [asyncio.create_task(foo()), asyncio.create_task(bar())]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    else:
        events = capture_events()

        with sentry_sdk.start_transaction(name="test_no_spans"):
            tasks = [asyncio.create_task(foo()), asyncio.create_task(bar())]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    sentry_sdk.flush()

    if span_streaming:
        segment = items.pop().payload
        assert segment["is_segment"] is True
        assert segment["name"] == "test_no_spans"

        spans = [item.payload for item in items]
        assert len(spans) == 0
    else:
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
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_delayed_enable_integration(
    sentry_init, capture_events, capture_items, span_streaming
):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    assert "asyncio" not in sentry_sdk.get_client().integrations

    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="test"):
            await asyncio.create_task(foo())

        sentry_sdk.flush()

        assert len(items) == 1
        assert items[0].payload.get("is_segment") is True

        items.clear()

    else:
        events = capture_events()

        with sentry_sdk.start_transaction(name="test"):
            await asyncio.create_task(foo())

        assert len(events) == 1
        (transaction,) = events
        assert not transaction["spans"]

    enable_asyncio_integration()

    assert "asyncio" in sentry_sdk.get_client().integrations

    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="test"):
            await asyncio.create_task(foo())

        sentry_sdk.flush()

        segment = items.pop().payload
        assert segment["is_segment"] is True

        spans = [item.payload for item in items]
        assert len(spans) == 1
        assert spans[0]["attributes"]["sentry.origin"] == "auto.function.asyncio"
    else:
        events = capture_events()

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
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_delayed_enable_integration_after_disabling(
    sentry_init, capture_events, capture_items, span_streaming
):
    sentry_init(
        disabled_integrations=[AsyncioIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    assert "asyncio" not in sentry_sdk.get_client().integrations

    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="test"):
            await asyncio.create_task(foo())

        sentry_sdk.flush()

        assert len(items) == 1
        assert items[0].payload.get("is_segment") is True

        items.clear()

    else:
        events = capture_events()

        with sentry_sdk.start_transaction(name="test"):
            await asyncio.create_task(foo())

        assert len(events) == 1
        (transaction,) = events
        assert not transaction["spans"]

    enable_asyncio_integration()

    assert "asyncio" in sentry_sdk.get_client().integrations

    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="test"):
            await asyncio.create_task(foo())

        sentry_sdk.flush()

        segment = items.pop().payload
        assert segment["is_segment"] is True

        spans = [item.payload for item in items]
        assert len(spans) == 1
        assert spans[0]["attributes"]["sentry.origin"] == "auto.function.asyncio"
    else:
        events = capture_events()

        with sentry_sdk.start_transaction(name="test"):
            await asyncio.create_task(foo())

        assert len(events) == 1
        (transaction,) = events
        assert transaction["spans"]
        assert transaction["spans"][0]["origin"] == "auto.function.asyncio"


@minimum_python_39
@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_internal_tasks_not_wrapped(
    sentry_init, capture_events, capture_items, span_streaming
):
    from sentry_sdk.utils import mark_sentry_task_internal

    sentry_init(
        integrations=[AsyncioIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    async def user_task():
        await asyncio.sleep(0.01)
        return "user_result"

    async def internal_task():
        await asyncio.sleep(0.01)
        return "internal_result"

    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="test_streamed_span"):
            user_task_obj = asyncio.create_task(user_task())

            with mark_sentry_task_internal():
                internal_task_obj = asyncio.create_task(internal_task())

            user_result = await user_task_obj
            internal_result = await internal_task_obj

    else:
        events = capture_events()

        with sentry_sdk.start_transaction(name="test_transaction"):
            user_task_obj = asyncio.create_task(user_task())

            with mark_sentry_task_internal():
                internal_task_obj = asyncio.create_task(internal_task())

            user_result = await user_task_obj
            internal_result = await internal_task_obj

    assert user_result == "user_result"
    assert internal_result == "internal_result"

    sentry_sdk.flush()

    if span_streaming:
        assert len(items) == 2

        segment = items.pop().payload
        assert segment["is_segment"] is True
        assert segment["name"] == "test_streamed_span"

        spans = [item.payload for item in items]
        assert len(spans) == 1
        assert spans[0]["name"].endswith("user_task")
    else:
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


parametrize_eager = pytest.mark.parametrize(
    "eager",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(
                sys.version_info < (3, 12),
                reason="Eager task factories were introduced in Python 3.12",
            ),
        ),
    ],
)


def run_in_patched_loop(main, eager=False):
    """
    Runs main() in a new event loop patched by the AsyncioIntegration. With
    eager=True, the loop has an eager task factory when it gets patched.
    """

    async def runner():
        if eager:
            asyncio.get_running_loop().set_task_factory(asyncio.eager_task_factory)
        # The integration is only set up once per process, so patch this loop
        patch_asyncio()
        return await main()

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(runner())
    finally:
        loop.close()


def start_root_span(span_streaming, name):
    if span_streaming:
        return sentry_sdk.traces.start_span(name=name)
    return sentry_sdk.start_transaction(name=name)


def start_child_span(span_streaming, name):
    if span_streaming:
        return sentry_sdk.traces.start_span(name=name)
    return sentry_sdk.start_span(op=name, name=name)


def get_current_span_name(span_streaming):
    if span_streaming:
        return sentry_sdk.traces.get_current_span().name
    return sentry_sdk.get_current_span().description


def get_spans_by_name(span_streaming, items, events):
    if span_streaming:
        return {item.payload["name"]: item.payload for item in items}

    (event,) = events
    spans = {span["description"]: span for span in event["spans"]}
    spans[event["transaction"]] = event["contexts"]["trace"]
    return spans


@minimum_python_38
@parametrize_eager
@pytest.mark.parametrize("span_streaming", [True, False])
def test_task_span_parent_is_span_current_at_create_task(
    sentry_init, capture_events, capture_items, span_streaming, eager
):
    sentry_init(
        integrations=[AsyncioIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
    )
    items = capture_items("span")
    events = capture_events()

    async def child():
        await asyncio.sleep(0)
        with start_child_span(span_streaming, "inner"):
            await asyncio.sleep(0)

    async def main():
        with start_root_span(span_streaming, "request"):
            with start_child_span(span_streaming, "handler"):
                task = asyncio.create_task(child())

                with start_child_span(span_streaming, "db.query"):
                    await asyncio.sleep(0)
                    # The task's spans don't become current in the creator
                    span_name_during_task = get_current_span_name(span_streaming)
                    await task

        return span_name_during_task

    span_name_during_task = run_in_patched_loop(main, eager=eager)
    sentry_sdk.flush()

    assert span_name_during_task == "db.query"

    spans = get_spans_by_name(span_streaming, items, events)
    assert spans[child.__qualname__]["parent_span_id"] == spans["handler"]["span_id"]
    assert spans["inner"]["parent_span_id"] == spans[child.__qualname__]["span_id"]


@minimum_python_38
@parametrize_eager
@pytest.mark.parametrize("span_streaming", [True, False])
def test_task_span_parent_ended_before_task_runs(
    sentry_init, capture_events, capture_items, span_streaming, eager
):
    sentry_init(
        integrations=[AsyncioIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
    )
    items = capture_items("span")
    events = capture_events()

    async def child():
        await asyncio.sleep(0)
        with start_child_span(span_streaming, "inner"):
            pass

    async def main():
        with start_root_span(span_streaming, "request"):
            with start_child_span(span_streaming, "handler"):
                task = asyncio.create_task(child())

            await task

    run_in_patched_loop(main, eager=eager)
    sentry_sdk.flush()

    spans = get_spans_by_name(span_streaming, items, events)
    assert spans[child.__qualname__]["parent_span_id"] == spans["handler"]["span_id"]
    assert spans["inner"]["parent_span_id"] == spans[child.__qualname__]["span_id"]


@minimum_python_38
@parametrize_eager
def test_task_span_created_when_segment_ended_before_task_runs(
    sentry_init, capture_items, eager
):
    sentry_init(
        integrations=[AsyncioIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    async def child():
        await asyncio.sleep(0)
        with sentry_sdk.traces.start_span(name="inner"):
            pass

    async def main():
        with sentry_sdk.traces.start_span(name="handler"):
            task = asyncio.create_task(child())

        await task

    run_in_patched_loop(main, eager=eager)
    sentry_sdk.flush()

    spans = get_spans_by_name(True, items, None)
    assert set(spans) == {"handler", child.__qualname__, "inner"}
    assert spans[child.__qualname__]["parent_span_id"] == spans["handler"]["span_id"]
    assert spans["inner"]["parent_span_id"] == spans[child.__qualname__]["span_id"]
    assert [name for name, span in spans.items() if span["is_segment"]] == ["handler"]
    assert len({span["trace_id"] for span in spans.values()}) == 1


@minimum_python_38
@parametrize_eager
def test_task_current_scope_forked_at_create_task(sentry_init, capture_events, eager):
    sentry_init(integrations=[AsyncioIntegration()])
    events = capture_events()

    async def handle(job_id):
        await asyncio.sleep(0)
        sentry_sdk.capture_message(f"job {job_id}")

    async def main():
        with sentry_sdk.isolation_scope():
            tasks = []
            for job_id in ("1", "2", "3"):
                sentry_sdk.get_current_scope().set_tag("job_id", job_id)
                tasks.append(asyncio.create_task(handle(job_id)))

            await asyncio.gather(*tasks)

    run_in_patched_loop(main, eager=eager)

    assert sorted((event["message"], event["tags"]["job_id"]) for event in events) == [
        ("job 1", "1"),
        ("job 2", "2"),
        ("job 3", "3"),
    ]


@minimum_python_38
@parametrize_eager
def test_task_isolation_scope_forked_when_task_runs(sentry_init, capture_events, eager):
    sentry_init(integrations=[AsyncioIntegration()])
    events = capture_events()

    async def capture():
        sentry_sdk.capture_message("task")

    async def main():
        with sentry_sdk.isolation_scope():
            sentry_sdk.set_tag("isolation_tag", "before")
            task = asyncio.create_task(capture())
            sentry_sdk.set_tag("isolation_tag", "after")
            await task

    run_in_patched_loop(main, eager=eager)

    # The isolation scope is still forked when the task first runs, which is
    # when create_task() is called for eager tasks
    (event,) = events
    assert event["tags"] == {"isolation_tag": "before" if eager else "after"}


@minimum_python_38
@parametrize_eager
def test_task_scope_changes_do_not_leak(sentry_init, capture_events, eager):
    sentry_init(integrations=[AsyncioIntegration()])
    events = capture_events()

    async def child(name):
        sentry_sdk.set_tag("isolation_tag", name)
        sentry_sdk.get_current_scope().set_tag("current_tag", name)
        await asyncio.sleep(0)
        sentry_sdk.capture_message(name)

    async def main():
        with sentry_sdk.isolation_scope():
            await asyncio.gather(
                asyncio.create_task(child("child 1")),
                asyncio.create_task(child("child 2")),
            )
            sentry_sdk.capture_message("creator")

    run_in_patched_loop(main, eager=eager)

    tags = {event["message"]: event.get("tags", {}) for event in events}
    assert tags == {
        "child 1": {"isolation_tag": "child 1", "current_tag": "child 1"},
        "child 2": {"isolation_tag": "child 2", "current_tag": "child 2"},
        "creator": {},
    }


@minimum_python_311
@parametrize_eager
def test_task_current_scope_forked_from_explicit_context(
    sentry_init, capture_events, eager
):
    sentry_init(integrations=[AsyncioIntegration()])
    events = capture_events()

    async def capture():
        await asyncio.sleep(0)
        sentry_sdk.capture_message("task")

    def set_tags(value):
        sentry_sdk.set_tag("isolation_source", value)
        sentry_sdk.get_current_scope().set_tag("current_source", value)

    async def main():
        with sentry_sdk.isolation_scope() as isolation_scope:
            set_tags("context")
            current_scope = sentry_sdk.get_current_scope()
            context = copy_context()

        with sentry_sdk.isolation_scope():
            set_tags("caller")
            task = asyncio.create_task(capture(), context=context)
            isolation_scope.set_tag("isolation_source", "changed")
            current_scope.set_tag("current_source", "changed")
            await task

    run_in_patched_loop(main, eager=eager)

    # Only the current scope is forked at create_task(), the isolation scope
    # is still forked when the task first runs
    (event,) = events
    assert event["tags"] == {
        "isolation_source": "context" if eager else "changed",
        "current_source": "context",
    }


@minimum_python_311
@parametrize_eager
@pytest.mark.parametrize("has_current_scope", [True, False])
def test_task_current_scope_forked_from_partial_explicit_context(
    sentry_init, capture_events, has_current_scope, eager
):
    sentry_init(integrations=[AsyncioIntegration()])
    events = capture_events()

    async def capture():
        await asyncio.sleep(0)
        sentry_sdk.capture_message("task")

    async def main():
        sentry_sdk.get_current_scope().set_tag("source", "caller")

        scope = Scope(ty=ScopeType.CURRENT)
        scope.set_tag("source", "context")
        context = Context()
        if has_current_scope:
            context.run(_current_scope.set, scope)

        task = asyncio.create_task(capture(), context=context)
        scope.set_tag("source", "changed")
        await task

    run_in_patched_loop(main, eager=eager)

    (event,) = events
    if has_current_scope:
        assert event["tags"] == {"source": "context"}
    else:
        assert "tags" not in event


@minimum_python_311
def test_task_current_scope_forked_from_entered_context(sentry_init, capture_events):
    sentry_init(integrations=[AsyncioIntegration()])
    events = capture_events()

    async def capture():
        await asyncio.sleep(0)
        sentry_sdk.capture_message("task")

    async def main():
        with sentry_sdk.isolation_scope():
            current_scope = sentry_sdk.get_current_scope()
            current_scope.set_tag("source", "context")
            context = copy_context()

        # The context is entered while create_task() is called with it. Eager
        # tasks can't be started in an entered context, with or without Sentry.
        task = context.run(asyncio.create_task, capture(), context=context)
        current_scope.set_tag("source", "changed")
        await task

    run_in_patched_loop(main)

    (event,) = events
    assert event["tags"] == {"source": "context"}


@minimum_python_38
@pytest.mark.parametrize("span_streaming", [True, False])
def test_task_cancelled_before_first_run(
    sentry_init, capture_events, capture_items, span_streaming
):
    sentry_init(
        integrations=[AsyncioIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
    )
    items = capture_items("span")
    events = capture_events()

    async def child():
        sentry_sdk.set_tag("from_task", "yes")

    async def main():
        with start_root_span(span_streaming, "request"):
            with start_child_span(span_streaming, "handler"):
                coro = child()
                task = asyncio.create_task(coro)
                task.cancel()

                with pytest.raises(asyncio.CancelledError):
                    await task
                # The task never started, so close the coroutine to avoid an
                # unawaited coroutine warning
                coro.close()

                span_name_after_task = get_current_span_name(span_streaming)

            sentry_sdk.capture_message("creator")

        return span_name_after_task

    span_name_after_task = run_in_patched_loop(main)
    sentry_sdk.flush()

    assert span_name_after_task == "handler"

    spans = get_spans_by_name(
        span_streaming, items, [e for e in events if e.get("type") == "transaction"]
    )
    assert set(spans) == {"request", "handler"}

    (message_event,) = [e for e in events if e.get("type") != "transaction"]
    assert "from_task" not in message_event.get("tags", {})


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
