import threading
import os

from time import sleep, time
from ._compat import queue, check_thread_support
from .utils import logger


_TERMINATOR = object()


class BackgroundWorker(object):
    def __init__(
        self, shutdown_timeout=10, initial_timeout=0.2, shutdown_callback=None
    ):
        check_thread_support()
        self._queue = queue.Queue(-1)
        self._lock = threading.Lock()
        self._thread = None
        self._thread_for_pid = None
        self.initial_timeout = initial_timeout
        self.shutdown_timeout = shutdown_timeout
        self.shutdown_callback = shutdown_callback

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
        logger.debug("Transport got kill request")
        with self._lock:
            if self._thread:
                self._queue.put_nowait(_TERMINATOR)
                self._thread = None
                self._thread_for_pid = None

    def shutdown(self):
        logger.debug("Transport got shutdown request")
        with self._lock:
            if not self.is_alive:
                return
            self._queue.put_nowait(_TERMINATOR)
            timeout = self.shutdown_timeout
            initial_timeout = min(self.initial_timeout, timeout)
            if not self._timed_queue_join(initial_timeout):
                pending = self._queue.qsize()
                logger.debug("%d event(s) pending on shutdown", pending)
                if self.shutdown_callback is not None:
                    self.shutdown_callback(pending, timeout)
                self._timed_queue_join(timeout - initial_timeout)
            self._thread = None
        logger.debug("Transport shut down")

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
