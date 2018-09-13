import threading
import os

from time import sleep, time
from sentry_sdk._compat import queue, check_thread_support
from sentry_sdk.utils import logger


_TERMINATOR = object()


class BackgroundWorker(object):
    def __init__(self):
        check_thread_support()
        self._queue = queue.Queue(-1)
        self._lock = threading.Lock()
        self._thread = None
        self._thread_for_pid = None

    @property
    def is_alive(self):
        if self._thread_for_pid != os.getpid():
            return False
        return self._thread and self._thread.is_alive()

    def _ensure_thread(self):
        if not self.is_alive:
            self.start()

    def _timed_queue_join(self, timeout):
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
        with self._lock:
            if not self.is_alive:
                self._thread = threading.Thread(
                    target=self._target, name="raven-sentry.BackgroundWorker"
                )
                self._thread.setDaemon(True)
                self._thread.start()
                self._thread_for_pid = os.getpid()

    def kill(self):
        logger.debug("background worker got kill request")
        with self._lock:
            if self._thread:
                self._queue.put_nowait(_TERMINATOR)
                self._thread = None
                self._thread_for_pid = None

    def shutdown(self, timeout, callback=None):
        logger.debug("background worker got shutdown request")
        with self._lock:
            if self.is_alive:
                self._queue.put_nowait(_TERMINATOR)
                if timeout > 0.0:
                    self._wait_shutdown(timeout, callback)
            self._thread = None
            self._thread_for_pid = None
        logger.debug("background worker shut down")

    def _wait_shutdown(self, timeout, callback):
        initial_timeout = min(0.1, timeout)
        if not self._timed_queue_join(initial_timeout):
            pending = self._queue.qsize()
            logger.debug("%d event(s) pending on shutdown", pending)
            if callback is not None:
                callback(pending, timeout)
            self._timed_queue_join(timeout - initial_timeout)

    def submit(self, callback):
        self._ensure_thread()
        self._queue.put_nowait(callback)

    def _target(self):
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
