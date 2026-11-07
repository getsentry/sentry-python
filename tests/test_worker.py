import asyncio
import threading

import pytest

from sentry_sdk._compat import PY38
from sentry_sdk.worker import AsyncWorker, BackgroundWorker


def _stop_background_worker(worker):
    thread = worker._thread
    worker.kill()
    if thread is not None:
        thread.join(timeout=1)
        assert not thread.is_alive()


@pytest.fixture
def background_worker():
    worker = BackgroundWorker()
    yield worker
    _stop_background_worker(worker)


async def _stop_async_worker(worker):
    worker.kill()
    await asyncio.sleep(0)


def test_background_worker_processes_callbacks_and_flushes(background_worker):
    processed = threading.Event()

    assert not background_worker.is_alive
    assert background_worker.submit(processed.set)
    assert background_worker.is_alive

    background_worker.flush(timeout=1)

    assert processed.is_set()
    assert background_worker.is_alive


def test_background_worker_reports_full_queue_and_rejects_extra_work():
    worker = BackgroundWorker(queue_size=1)
    started = threading.Event()
    release = threading.Event()

    def block_worker():
        started.set()
        release.wait()

    try:
        assert worker.submit(block_worker)
        assert started.wait(timeout=1)

        assert worker.submit(lambda: None)
        assert worker.full()
        assert not worker.submit(lambda: None)

        release.set()
        worker.flush(timeout=1)
        assert not worker.full()
    finally:
        release.set()
        _stop_background_worker(worker)


def test_background_worker_continues_after_callback_error(background_worker):
    processed = threading.Event()

    def fail():
        raise ValueError("expected callback failure")

    assert background_worker.submit(fail)
    assert background_worker.submit(processed.set)
    background_worker.flush(timeout=1)

    assert processed.is_set()
    assert background_worker.is_alive


def test_background_worker_flush_calls_callback_while_work_is_pending(
    background_worker,
):
    started = threading.Event()
    release = threading.Event()
    callback_called = threading.Event()
    callback_args = []

    def block_worker():
        started.set()
        release.wait()

    def on_flush(pending, timeout):
        callback_args.append((pending, timeout))
        callback_called.set()

    assert background_worker.submit(block_worker)
    assert started.wait(timeout=1)
    assert background_worker.submit(lambda: None)

    flush_thread = threading.Thread(
        target=background_worker.flush, kwargs={"timeout": 1.0, "callback": on_flush}
    )
    flush_thread.start()

    try:
        assert callback_called.wait(timeout=1)
    finally:
        release.set()

    flush_thread.join(timeout=1)
    assert not flush_thread.is_alive()
    assert callback_args == [(2, 1.0)]


def test_background_worker_flush_returns_when_work_times_out(background_worker):
    started = threading.Event()
    release = threading.Event()

    def block_worker():
        started.set()
        release.wait()

    assert background_worker.submit(block_worker)
    assert started.wait(timeout=1)

    try:
        background_worker.flush(timeout=0.15)
        assert not release.is_set()
        assert background_worker.is_alive
    finally:
        release.set()
        background_worker.flush(timeout=1)


@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
def test_async_worker_cannot_start_without_a_running_event_loop():
    worker = AsyncWorker()

    assert worker.full()
    assert not worker.submit(lambda: None)
    assert not worker.is_alive
    assert worker.flush(timeout=1) is None
    worker.kill()


@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
@pytest.mark.asyncio
async def test_async_worker_processes_callbacks_and_flushes():
    worker = AsyncWorker()
    processed = asyncio.Event()

    async def callback():
        processed.set()

    try:
        assert worker.full()
        assert worker.submit(callback)
        assert worker.is_alive
        flush_task = worker.flush(timeout=1)
        assert flush_task is not None
        await flush_task

        assert processed.is_set()
        assert worker.is_alive
    finally:
        await _stop_async_worker(worker)


@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
@pytest.mark.asyncio
async def test_async_worker_reports_full_queue_and_rejects_extra_work():
    worker = AsyncWorker(queue_size=1)
    started = asyncio.Event()
    release = asyncio.Event()
    processed = asyncio.Event()

    async def block_worker():
        started.set()
        await release.wait()

    async def mark_processed():
        processed.set()

    async def unused():
        pass

    try:
        assert worker.submit(block_worker)
        assert worker.full()
        assert not worker.submit(unused)

        await asyncio.wait_for(started.wait(), timeout=1)
        assert not worker.full()

        assert worker.submit(mark_processed)
        assert worker.full()
        assert not worker.submit(unused)

        release.set()
        flush_task = worker.flush(timeout=1)
        assert flush_task is not None
        await flush_task
        assert processed.is_set()
        assert not worker.full()
    finally:
        release.set()
        await _stop_async_worker(worker)


@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
@pytest.mark.asyncio
async def test_async_worker_continues_after_callback_error():
    worker = AsyncWorker()
    processed = asyncio.Event()

    async def fail():
        raise ValueError("expected callback failure")

    async def succeed():
        processed.set()

    try:
        assert worker.submit(fail)
        assert worker.submit(succeed)
        flush_task = worker.flush(timeout=1)
        assert flush_task is not None
        await flush_task

        assert processed.is_set()
        assert worker.is_alive
    finally:
        await _stop_async_worker(worker)


@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
@pytest.mark.asyncio
async def test_async_worker_flush_calls_callback_while_work_is_pending():
    worker = AsyncWorker()
    started = asyncio.Event()
    release = asyncio.Event()
    callback_called = asyncio.Event()
    callback_args = []

    async def block_worker():
        started.set()
        await release.wait()

    def on_flush(pending, timeout):
        callback_args.append((pending, timeout))
        callback_called.set()

    try:
        assert worker.submit(block_worker)
        await asyncio.wait_for(started.wait(), timeout=1)

        flush_task = worker.flush(timeout=1.0, callback=on_flush)
        assert flush_task is not None
        await asyncio.wait_for(callback_called.wait(), timeout=1)
        assert callback_args == [(1, 1.0)]

        release.set()
        await flush_task
    finally:
        release.set()
        await _stop_async_worker(worker)


@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
@pytest.mark.asyncio
async def test_async_worker_flush_returns_when_work_times_out():
    worker = AsyncWorker()
    started = asyncio.Event()
    release = asyncio.Event()

    async def block_worker():
        started.set()
        await release.wait()

    try:
        assert worker.submit(block_worker)
        await asyncio.wait_for(started.wait(), timeout=1)

        flush_task = worker.flush(timeout=0.15)
        assert flush_task is not None
        await flush_task
        assert not release.is_set()
        assert worker.is_alive

        release.set()
        flush_task = worker.flush(timeout=1)
        assert flush_task is not None
        await flush_task
    finally:
        release.set()
        await _stop_async_worker(worker)


@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
@pytest.mark.asyncio
async def test_async_worker_kill_cancels_active_callbacks():
    worker = AsyncWorker()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def block_worker():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    assert worker.submit(block_worker)
    await asyncio.wait_for(started.wait(), timeout=1)

    try:
        worker.kill()
        await asyncio.sleep(0)

        assert cancelled.is_set()
        assert not worker.is_alive
    finally:
        await _stop_async_worker(worker)
