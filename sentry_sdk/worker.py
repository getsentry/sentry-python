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

    def flush(self, timeout, callback=None):
        logger.debug("background worker got flush request")
        with self._lock:
            if self.is_alive and timeout > 0.0:
                self._wait_flush(timeout, callback)
        logger.debug("background worker flushed")

    def _wait_flush(self, timeout, callback):
        event = threading.Event()
        pending = self._queue.qsize()
        self._queue.put_nowait(event.set)
        logger.debug("%d event(s) pending on flush", pending)

        if callback is not None:
            callback(pending, timeout)

        event.wait(timeout)

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
