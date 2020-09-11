import os

from collections import deque
from threading import Thread
from time import sleep, time
from sentry_sdk._compat import check_thread_support
from sentry_sdk.utils import logger

from sentry_sdk._types import MYPY

if MYPY:
    from queue import Queue
    from typing import Any
    from typing import Optional
    from typing import Callable


_TERMINATOR = object()


class BackgroundWorker(object):
    def __init__(self):
        # type: () -> None
        check_thread_support()
        self._queue = deque(maxlen=30)
        self._thread = None  # type: Optional[Thread]
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

    def start(self):
        # type: () -> None
        if self.is_alive:
            return

        self._thread = Thread(target=self._target, name="raven-sentry.BackgroundWorker")
        self._thread.setDaemon(True)
        self._thread.start()
        self._thread_for_pid = os.getpid()

    def kill(self):
        # type: () -> None
        """
        Kill worker thread. Returns immediately. Not useful for
        waiting on shutdown for events, use `flush` for that.

        It is assumed that after this method, nothing is called. `Client.close`
        calls flush and kill in that order.
        """
        logger.debug("background worker got kill request")
        if self._thread:
            try:
                self._queue.appendleft(_TERMINATOR)
            except queue.Full:
                logger.debug("background worker queue full, kill failed")

            self._thread = None
            self._thread_for_pid = None

    def flush(self, timeout, callback=None):
        # type: (float, Optional[Callable[[int, float]]]) -> None
        logger.debug("background worker got flush request")
        self._wait_flush(timeout, callback)
        logger.debug("background worker flushed")

    def _wait_flush(self, timeout, callback):
        # type: (float, Optional[Callable[[int, float]]]) -> None
        if not self.is_alive:
            if len(self._queue):
                self._ensure_thread()
            else:
                return

        initial_timeout = min(0.1, timeout)
        sleep(initial_timeout)
        timeout -= initial_timeout

        if not self.is_alive:
            return

        pending = len(self._queue)
        logger.debug("%d event(s) pending on flush", pending + 1)
        if callback is not None:
            callback(pending, timeout)

        while self.is_alive and timeout > 0:
            sleep(0.1)
            timeout -= 0.1

    def submit(self, callback):
        # type: (Callable[[], None]) -> None
        if len(self._queue):
            logger.debug("background worker queue full, dropping event")

        self._queue.appendleft(callback)
        # Run thread after appending such that it does not terminate
        # immediately.
        self._ensure_thread()

    def _target(self):
        # type: () -> None
        while True:
            try:
                callback = self._queue.pop()
            except IndexError:
                break

            if callback is _TERMINATOR:
                break
            try:
                callback()
            except Exception:
                logger.error("Failed processing job", exc_info=True)

            sleep(0)
