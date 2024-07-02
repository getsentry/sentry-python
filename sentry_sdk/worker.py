import os
import threading

from time import sleep, time
from multiprocessing import get_context
from sentry_sdk._queue import Queue, FullError
from sentry_sdk.utils import capture_internal_exceptions, logger
from sentry_sdk.consts import DEFAULT_QUEUE_SIZE

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from typing import Optional
    from typing import Callable

    from multiprocessing.connection import Connection
    from multiprocessing.context import BaseContext


_TERMINATOR = object()


class _JobProducer:
    def __init__(self, connection, context):
        # type: (Connection, BaseContext) -> None
        self._conn = connection
        self._pending = 0
        self._lock = context.Lock()

    def _update_pending(self):
        # type: () -> None
        """Must be called with self._lock held."""
        while self._conn.poll():
            self._conn.recv()
            self._pending -= 1

    def submit(self, obj):
        # type: (object) -> None
        with self._lock:
            # breakpoint()
            self._update_pending()
            self._pending += 1
            self._conn.send(obj)

    def pending(self):
        # type: () -> int
        with self._lock:
            self._update_pending()
            return self._pending

    def close(self):
        # type: () -> None
        self._conn.close()


class _JobConsumer:
    def __init__(self, connection):
        # type: (Connection) -> None
        self._conn = connection

    def consume_until_close(self):
        while True:
            try:
                obj = self._conn.recv()
            except EOFError:
                break

            yield obj

            # Acknowledge that we received the object
            self._conn.send(None)

            sleep(0)


def _make_job_queue(ctx):
    # type: (BaseContext) -> tuple[_JobProducer, _JobConsumer]
    a, b = ctx.Pipe()
    return _JobProducer(a, ctx), _JobConsumer(b)


def dummy():
    pass


class BackgroundWorker:
    def __init__(self, sender=lambda _: None, queue_size=DEFAULT_QUEUE_SIZE):
        # type: (Any, int) -> None
        self._mp_ctx = get_context("fork")
        self._queue_producer, self._queue_consumer = _make_job_queue(self._mp_ctx)
        self._process_for_pid = None  # type: Optional[int]
        self.start(sender)

    @property
    def is_alive(self):
        # type: () -> bool
        if self._process_for_pid != os.getpid():
            return False

        return self._process.is_alive()

    def _timed_queue_join(self, timeout):
        # type: (float) -> bool

        sleep(timeout)
        if self._queue_producer.pending() == 0:
            return True

        return False
        # deadline = time() + timeout
        # queue = self._queue

        # queue.all_tasks_done.acquire()

        # try:
        #     while queue.unfinished_tasks:
        #         delay = deadline - time()
        #         if delay <= 0:
        #             return False
        #         queue.all_tasks_done.wait(timeout=delay)

        #     return True
        # finally:
        #     queue.all_tasks_done.release()

    def start(self, sender=lambda _: None):
        # type: (Any) -> None
        if not self.is_alive:
            print("starting process")
            self._process = self._mp_ctx.Process(
                target=self._target, name="sentry-sdk.BackgroundWorker", args=(sender,)
            )
            self._process.daemon = True
            self._process.start()
            self._process_for_pid = os.getpid()

    def kill(self):
        # type: () -> None
        """
        Kill worker thread. Returns immediately. Not useful for
        waiting on shutdown for events, use `flush` for that.

        No other methods may be called after this one; the
        behavior of doing so is undefined. We recommend destroying
        all references to the worker after calling this method.
        """
        logger.debug("background worker got kill request")
        if self._process:
            try:
                self._queue_producer.close()
            except FullError:
                logger.debug("background worker queue full, kill failed")

    def flush(self, timeout, callback=None):
        # type: (float, Optional[Any]) -> None
        logger.debug("background worker got flush request")
        if self.is_alive and timeout > 0.0:
            self._wait_flush(timeout, callback)
        logger.debug("background worker flushed")

    def full(self):
        # type: () -> bool
        return False
        # TODO: Update
        # return self._queue.full()

    def _wait_flush(self, timeout, callback):
        # type: (float, Optional[Any]) -> Nonex
        initial_timeout = min(0.1, timeout)
        if not self._timed_queue_join(initial_timeout):
            pending = self._queue_producer.pending()
            logger.debug("%d event(s) pending on flush", pending)
            if callback is not None:
                callback(pending, timeout)

            if not self._timed_queue_join(timeout - initial_timeout):
                pending = self._queue_producer.pending()
                logger.error("flush timed out, dropped %s events", pending)

    def submit(self, callback):
        # type: (Callable[[], None]) -> bool
        try:
            self._queue_producer.submit(callback)
            return True
        except FullError:
            return False

    def _target(self, sender):
        # type: (Any) -> None
        for envelope in self._queue_consumer.consume_until_close():
            print("got envelope")
            print(envelope)
            try:
                print(sender)
                sender(envelope)
            except Exception:
                logger.error("Failed processing job", exc_info=True)
