import os
import threading

from time import sleep, time
from sentry_sdk._compat import check_thread_support
from sentry_sdk._queue import Queue, Full
from sentry_sdk.utils import logger

from sentry_sdk._types import MYPY

if MYPY:
    from typing import Optional
    from typing import Callable
    from typing import Dict
    from typing import Any

    from sentry_sdk._types import FlushCallback


_TERMINATOR = object()


class Worker(object):
    """
    Base interface for task queue based worker.
    """

    @property
    def is_alive(self):
        # type: () -> bool
        return False

    def start(self):
        # type: () -> None
        """Start task queue processing."""
        raise NotImplementedError()

    def kill(self):
        # type: () -> None
        """
        Stop task queue processing. Returns immediately. Not useful
        for waiting on shutdown for events, use `flush` for that.
        """

    def flush(self, timeout, callback=None):
        # type: (float, Optional[FlushCallback]) -> None
        """Wait `timeout` seconds for the current tasks to be finished."""
        pass

    def submit(self, callback):
        # type: (Callable[[], None]) -> None
        """Submit new task to the queue"""
        raise NotImplementedError()


class BackgroundWorker(Worker):
    def __init__(self, queue_size=30):
        # type: (int) -> None
        check_thread_support()
        self._queue = Queue(queue_size)  # type: Queue
        self._lock = threading.Lock()
        self._thread = None  # type: Optional[threading.Thread]
        self._thread_for_pid = None  # type: Optional[int]

    @property
    def is_alive(self):
        # type: () -> bool
        if self._thread_for_pid != os.getpid():
            return False
        if not self._thread:
            return False
        return self._thread.is_alive()

    def _ensure_thread(self):
        # type: () -> None
        if not self.is_alive:
            self.start()

    def _timed_queue_join(self, timeout):
        # type: (float) -> bool
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

    def start(self):
        # type: () -> None
        with self._lock:
            if not self.is_alive:
                self._thread = threading.Thread(
                    target=self._target, name="sentry_sdk.BackgroundWorker"
                )
                self._thread.setDaemon(True)
                self._thread.start()
                self._thread_for_pid = os.getpid()

    def kill(self):
        # type: () -> None
        # FIXME: This doesn't actually do what it claims. Putting the terminator
        #        on the queue means it will execute all the other tasks first.
        """
        Kill worker thread. Returns immediately. Not useful for
        waiting on shutdown for events, use `flush` for that.
        """
        logger.debug("background worker got kill request")
        with self._lock:
            if self._thread:
                try:
                    self._queue.put_nowait(_TERMINATOR)
                except Full:
                    logger.debug("background worker queue full, kill failed")

                self._thread = None
                self._thread_for_pid = None

    def flush(self, timeout, callback=None):
        # type: (float, Optional[FlushCallback]) -> None
        logger.debug("background worker got flush request")
        with self._lock:
            if self.is_alive and timeout > 0.0:
                self._wait_flush(timeout, callback)
        logger.debug("background worker flushed")

    def _wait_flush(self, timeout, callback):
        # type: (float, Optional[FlushCallback]) -> None
        initial_timeout = min(0.1, timeout)
        if not self._timed_queue_join(initial_timeout):
            pending = self._queue.qsize()
            logger.debug("%d event(s) pending on flush", pending)
            if callback is not None:
                callback(pending, timeout)
            self._timed_queue_join(timeout - initial_timeout)

    def submit(self, callback):
        # type: (Callable[[], None]) -> None
        self._ensure_thread()
        try:
            self._queue.put_nowait(callback)
        except Full:
            logger.debug("background worker queue full, dropping event")

    def _target(self):
        # type: () -> None
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


def make_worker(options):
    # type: (Dict[str, Any]) -> Optional[Worker]
    ref_worker = options["worker"]

    # If no worker is given, we use the background worker class
    if ref_worker is None:
        return BackgroundWorker()
    elif isinstance(ref_worker, Worker):
        return ref_worker
    elif isinstance(ref_worker, type) and issubclass(ref_worker, Worker):
        return ref_worker()
    else:
        return None
