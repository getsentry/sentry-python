import os
import time
import weakref
from threading import Lock, Thread
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.utils import logger

if TYPE_CHECKING:
    from typing import Optional


MAX_DOWNSAMPLE_FACTOR = 10


class Monitor:
    """
    Performs health checks in a separate thread once every interval seconds
    and updates the internal state. Other parts of the SDK only read this state
    and act accordingly.
    """

    name = "sentry.monitor"

    _thread: "Optional[Thread]"
    _thread_for_pid: "Optional[int]"

    def __init__(
        self, transport: "sentry_sdk.transport.Transport", interval: float = 10
    ) -> None:
        self.transport: "sentry_sdk.transport.Transport" = transport
        self.interval: float = interval

        self._healthy = True
        self._downsample_factor: int = 0
        self._running = True
        self._reset_thread_state()

        # See https://github.com/getsentry/sentry-python/issues/6148.
        # If os.fork() runs while another thread holds self._thread_lock,
        # the child inherits the lock locked but the holding thread does
        # not exist in the child, so the lock can never be released and
        # _ensure_running deadlocks forever. Reinitialise the lock and
        # cached thread/pid in the child so it starts clean regardless
        # of inherited state. We bind via a WeakMethod so the
        # permanently-registered fork handler does not pin this Monitor
        # (and its Transport): register_at_fork has no unregister API.
        # POSIX-only; Windows uses spawn.
        if hasattr(os, "register_at_fork"):
            weak_reset = weakref.WeakMethod(self._reset_thread_state)

            def _reset_in_child() -> None:
                method = weak_reset()
                if method is not None:
                    method()

            os.register_at_fork(after_in_child=_reset_in_child)

    def _reset_thread_state(self) -> None:
        self._thread = None
        self._thread_lock = Lock()
        self._thread_for_pid = None

    def _ensure_running(self) -> None:
        """
        Check that the monitor has an active thread to run in, or create one if not.

        Note that this might fail (e.g. in Python 3.12 it's not possible to
        spawn new threads at interpreter shutdown). In that case self._running
        will be False after running this function.
        """
        if self._thread_for_pid == os.getpid() and self._thread is not None:
            return None

        with self._thread_lock:
            if self._thread_for_pid == os.getpid() and self._thread is not None:
                return None

            def _thread() -> None:
                while self._running:
                    time.sleep(self.interval)
                    if self._running:
                        self.run()

            thread = Thread(name=self.name, target=_thread)
            thread.daemon = True
            try:
                thread.start()
            except RuntimeError:
                # Unfortunately at this point the interpreter is in a state that no
                # longer allows us to spawn a thread and we have to bail.
                self._running = False
                return None

            self._thread = thread
            self._thread_for_pid = os.getpid()

        return None

    def run(self) -> None:
        self.check_health()
        self.set_downsample_factor()

    def set_downsample_factor(self) -> None:
        if self._healthy:
            if self._downsample_factor > 0:
                logger.debug(
                    "[Monitor] health check positive, reverting to normal sampling"
                )
            self._downsample_factor = 0
        else:
            if self.downsample_factor < MAX_DOWNSAMPLE_FACTOR:
                self._downsample_factor += 1
            logger.debug(
                "[Monitor] health check negative, downsampling with a factor of %d",
                self._downsample_factor,
            )

    def check_health(self) -> None:
        """
        Perform the actual health checks,
        currently only checks if the transport is rate-limited.
        TODO: augment in the future with more checks.
        """
        self._healthy = self.transport.is_healthy()

    def is_healthy(self) -> bool:
        self._ensure_running()
        return self._healthy

    @property
    def downsample_factor(self) -> int:
        self._ensure_running()
        return self._downsample_factor

    def kill(self) -> None:
        self._running = False
