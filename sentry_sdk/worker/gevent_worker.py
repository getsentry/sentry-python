from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.worker import Worker
from sentry_sdk.utils import logger

try:
    import gevent  # type: ignore

    from gevent.event import Event  # type: ignore
    from gevent.lock import RLock  # type: ignore
    from gevent.queue import Empty, Full, JoinableQueue  # type: ignore
except ImportError:
    raise DidNotEnable("gevent not installed")

from sentry_sdk._types import MYPY

if MYPY:
    from typing import Optional
    from typing import Callable

    from sentry_sdk._types import FlushCallback


class GeventWorker(Worker):
    def __init__(self, queue_size=30):
        # type: (int) -> None
        self._queue_size = queue_size
        self._queue = JoinableQueue(self._queue_size)
        self._kill_event = Event()
        self._lock = RLock()
        self._greenlet = None

    @property
    def is_alive(self):
        # type: () -> bool
        if not self._greenlet:
            return False
        return not self._greenlet.dead

    def _ensure_greenlet(self):
        # type: () -> None
        if not self.is_alive:
            self.start()

    def start(self):
        # type: () -> None
        with self._lock:
            if not self.is_alive:
                self._greenlet = gevent.spawn(_task_loop, self._queue, self._kill_event)

    def kill(self):
        # type: () -> None
        logger.debug("gevent worker got kill request")
        with self._lock:
            if self._greenlet:
                self._kill_event.set()
                self._greenlet.join(timeout=0.5)
                if not self._greenlet.dead:
                    # Forcibly kill greenlet
                    logger.warning("gevent worker failed to terminate gracefully.")
                    self._greenlet.kill(block=False)
                self._greenlet = None
                self._queue = JoinableQueue(self._queue_size)
                self._kill_event = Event()

    def flush(self, timeout, callback=None):
        # type: (float, Optional[FlushCallback]) -> None
        logger.debug("gevent worker got flush request")
        with self._lock:
            if self.is_alive and timeout > 0.0:
                self._wait_flush(timeout, callback)
        logger.debug("gevent worker flushed")

    def _wait_flush(self, timeout, callback):
        # type: (float, Optional[FlushCallback]) -> None
        initial_timeout = min(0.1, timeout)
        if not self._queue.join(initial_timeout):
            pending = self._queue.qsize()
            logger.debug("%d event(s) pending on flush", pending)
            if callback is not None:
                callback(pending, timeout)
            self._queue.join(timeout - initial_timeout)

    def submit(self, callback):
        # type: (Callable[[], None]) -> None
        self._ensure_greenlet()
        try:
            self._queue.put_nowait(callback)
        except Full:
            logger.debug("gevent worker queue full, dropping event")


def _task_loop(queue, kill_event):
    # type: (JoinableQueue, Event) -> None
    while True:
        try:
            callback = queue.get(timeout=0.1)
            if kill_event.is_set():
                # NOTE: We want to kill before executing the task, but we also need
                #       to be able to kill on an empty queue, so we raise Empty to
                #       avoid code duplciation
                raise Empty
            try:
                callback()
            except Exception:
                logger.error("Failed processing job", exc_info=True)
            finally:
                queue.task_done()
        except Empty:
            pass

        if kill_event.is_set():
            logger.debug("gevent worker killed")
            break
        gevent.sleep(0)
