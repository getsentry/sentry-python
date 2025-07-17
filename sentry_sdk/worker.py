from __future__ import annotations
from abc import ABC, abstractmethod
import os
import threading
import asyncio
import inspect

from time import sleep, time
from sentry_sdk._queue import Queue, FullError
from sentry_sdk.utils import logger
from sentry_sdk.consts import DEFAULT_QUEUE_SIZE

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional, Callable


_TERMINATOR = object()


class Worker(ABC):
    """
    Base class for all workers.

    A worker is used to process events in the background and send them to Sentry.
    """

    @property
    @abstractmethod
    def is_alive(self) -> bool:
        pass

    @abstractmethod
    def kill(self) -> None:
        pass

    def flush(
        self, timeout: float, callback: Optional[Callable[[int, float], Any]] = None
    ) -> None:
        """
        Flush the worker.

        This method blocks until the worker has flushed all events or the specified timeout is reached.
        Default implementation is a no-op, since this method may only be relevant to some workers.
        Subclasses should override this method if necessary.
        """
        return None

    @abstractmethod
    def full(self) -> bool:
        pass

    @abstractmethod
    def submit(self, callback: Callable[[], Any]) -> bool:
        """
        Schedule a callback to be executed by the worker.

        Returns True if the callback was scheduled, False if the queue is full.
        """
        pass


class BackgroundWorker(Worker):
    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._queue: Queue = Queue(queue_size)
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._thread_for_pid: Optional[int] = None

    @property
    def is_alive(self) -> bool:
        if self._thread_for_pid != os.getpid():
            return False
        if not self._thread:
            return False
        return self._thread.is_alive()

    def _ensure_thread(self) -> None:
        if not self.is_alive:
            self.start()

    def _timed_queue_join(self, timeout: float) -> bool:
        deadline = time() + timeout
        queue = self._queue

        queue.all_tasks_done.acquire()

        try:
            while queue.unfinished_tasks:
                delay = deadline - time()
                if delay <= 0:
                    return False
                queue.all_tasks_done.wait(timeout=delay)

            return True
        finally:
            queue.all_tasks_done.release()

    def start(self) -> None:
        with self._lock:
            if not self.is_alive:
                self._thread = threading.Thread(
                    target=self._target, name="sentry-sdk.BackgroundWorker"
                )
                self._thread.daemon = True
                try:
                    self._thread.start()
                    self._thread_for_pid = os.getpid()
                except RuntimeError:
                    # At this point we can no longer start because the interpreter
                    # is already shutting down.  Sadly at this point we can no longer
                    # send out events.
                    self._thread = None

    def kill(self) -> None:
        """
        Kill worker thread. Returns immediately. Not useful for
        waiting on shutdown for events, use `flush` for that.
        """
        logger.debug("background worker got kill request")
        with self._lock:
            if self._thread:
                try:
                    self._queue.put_nowait(_TERMINATOR)
                except FullError:
                    logger.debug("background worker queue full, kill failed")

                self._thread = None
                self._thread_for_pid = None

    def flush(self, timeout: float, callback: Optional[Any] = None) -> None:
        logger.debug("background worker got flush request")
        with self._lock:
            if self.is_alive and timeout > 0.0:
                self._wait_flush(timeout, callback)
        logger.debug("background worker flushed")

    def full(self) -> bool:
        return self._queue.full()

    def _wait_flush(self, timeout: float, callback: Optional[Any]) -> None:
        initial_timeout = min(0.1, timeout)
        if not self._timed_queue_join(initial_timeout):
            pending = self._queue.qsize() + 1
            logger.debug("%d event(s) pending on flush", pending)
            if callback is not None:
                callback(pending, timeout)

            if not self._timed_queue_join(timeout - initial_timeout):
                pending = self._queue.qsize() + 1
                logger.error("flush timed out, dropped %s events", pending)

    def submit(self, callback: Callable[[], Any]) -> bool:
        self._ensure_thread()
        try:
            self._queue.put_nowait(callback)
            return True
        except FullError:
            return False

    def _target(self) -> None:
        while True:
            callback = self._queue.get()
            try:
                if callback is _TERMINATOR:
                    break
                try:
                    callback()
                except Exception:
                    logger.error("Failed processing job", exc_info=True)
            finally:
                self._queue.task_done()
            sleep(0)


class AsyncWorker(Worker):
    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._queue: asyncio.Queue[Callable[[], Any]] = asyncio.Queue(queue_size)
        self._task: Optional[asyncio.Task[None]] = None
        # Event loop needs to remain in the same process
        self._task_for_pid: Optional[int] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def is_alive(self) -> bool:
        if self._task_for_pid != os.getpid():
            return False
        if not self._task or not self._loop:
            return False
        return self._loop.is_running() and not self._task.done()

    def kill(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
            self._task_for_pid = None

    def start(self) -> None:
        if not self.is_alive:
            try:
                self._loop = asyncio.get_running_loop()
                self._task = self._loop.create_task(self._target())
                self._task_for_pid = os.getpid()
            except RuntimeError:
                # There is no event loop running
                self._loop = None
                self._task = None
                self._task_for_pid = None

    def full(self) -> bool:
        return self._queue.full()

    def _ensure_task(self) -> None:
        if not self.is_alive:
            self.start()

    async def _wait_flush(self, timeout: float, callback: Optional[Any] = None) -> None:
        if not self._loop or not self._loop.is_running():
            return

        initial_timeout = min(0.1, timeout)

        # Timeout on the join
        try:
            await asyncio.wait_for(self._queue.join(), timeout=initial_timeout)
        except asyncio.TimeoutError:
            pending = self._queue.qsize() + 1
            logger.debug("%d event(s) pending on flush", pending)
            if callback is not None:
                callback(pending, timeout)

            try:
                remaining_timeout = timeout - initial_timeout
                await asyncio.wait_for(self._queue.join(), timeout=remaining_timeout)
            except asyncio.TimeoutError:
                pending = self._queue.qsize() + 1
                logger.error("flush timed out, dropped %s events", pending)

    async def flush_async(self, timeout: float, callback: Optional[Any] = None) -> None:
        logger.debug("background worker got flush request")
        if self.is_alive and timeout > 0.0:
            await self._wait_flush(timeout, callback)
        logger.debug("background worker flushed")

    def submit(self, callback: Callable[[], Any]) -> bool:
        self._ensure_task()

        try:
            self._queue.put_nowait(callback)
            return True
        except asyncio.QueueFull:
            return False

    async def _target(self) -> None:
        while True:
            callback = await self._queue.get()
            try:
                if inspect.iscoroutinefunction(callback):
                    # Callback is an async coroutine, need to await it
                    await callback()
                else:
                    # Callback is a sync function, need to call it
                    callback()
            except Exception:
                logger.error("Failed processing job", exc_info=True)
            finally:
                self._queue.task_done()
            # Yield to let the event loop run other tasks
            await asyncio.sleep(0)
