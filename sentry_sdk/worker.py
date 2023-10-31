import os
import threading

from time import sleep, time
from sentry_sdk._compat import check_thread_support
from sentry_sdk._queue import Queue, FullError
from sentry_sdk.utils import logger
from sentry_sdk.consts import DEFAULT_QUEUE_SIZE

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from typing import Optional
    from typing import Callable


_TERMINATOR = object()


class BackgroundWorker(object):
    def __init__(self, queue_size=DEFAULT_QUEUE_SIZE):
        # type: (int) -> None
        check_thread_support()
        self._queue = Queue(queue_size)  # type: Queue
        self._lock = threading.Lock()
        self._thread = None  # type: Optional[threading.Thread]
        self._thread_for_pid = None  # type: Optional[int]
        self._can_start_threads = True  # type: bool

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

    def _get_main_thread(self):
        # type: () -> Optional[threading.Thread]
        main_thread = None

        try:
            main_thread = threading.main_thread()
        except AttributeError:
            # Python 2.7 doesn't have threading.main_thread()
            for thread in threading.enumerate():
                if isinstance(thread, threading._MainThread):  # type: ignore[attr-defined]
                    main_thread = thread
                    break

        return main_thread

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
                if self._can_start_threads:
                    self._thread = threading.Thread(
                        target=self._target, name="raven-sentry.BackgroundWorker"
                    )
                    self._thread.daemon = True
                    self._thread.start()
                else:
                    self._thread = self._get_main_thread()

                self._thread_for_pid = os.getpid()

    def kill(self):
        # type: () -> None
        """
        Kill worker thread. Returns immediately. Not useful for
        waiting on shutdown for events, use `flush` for that.
        """
        logger.debug("background worker got kill request")
        with self._lock:
            if self._thread and self._thread != self._get_main_thread():
                try:
                    self._queue.put_nowait(_TERMINATOR)
                except FullError:
                    logger.debug("background worker queue full, kill failed")

                self._thread = None
                self._thread_for_pid = None

    def flush(self, timeout, callback=None):
        # type: (float, Optional[Any]) -> None
        logger.debug("background worker got flush request")
        with self._lock:
            if self.is_alive and timeout > 0.0:
                self._wait_flush(timeout, callback)
        logger.debug("background worker flushed")

    def full(self):
        # type: () -> bool
        return self._queue.full()

    def _wait_flush(self, timeout, callback):
        # type: (float, Optional[Any]) -> None
        initial_timeout = min(0.1, timeout)
        if not self._timed_queue_join(initial_timeout):
            pending = self._queue.qsize() + 1
            logger.debug("%d event(s) pending on flush", pending)
            if callback is not None:
                callback(pending, timeout)

            if not self._timed_queue_join(timeout - initial_timeout):
                pending = self._queue.qsize() + 1
                logger.error("flush timed out, dropped %s events", pending)

    def submit(self, callback):
        # type: (Callable[[], None]) -> bool
        self._ensure_thread()
        try:
            self._queue.put_nowait(callback)
            return True
        except FullError:
            return False

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

    def prepare_shutdown(self):
        # type: () -> None
        # If the Python 3.12+ interpreter is shutting down, trying to start a new
        # thread throws a RuntimeError. If we're shutting down and the worker has
        # no active thread, use the main thread instead of spawning a new one.
        # See https://github.com/python/cpython/pull/104826
        self._can_start_threads = False
