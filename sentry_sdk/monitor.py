import os
import time
from threading import Thread, Lock

import sentry_sdk
from sentry_sdk.utils import logger
from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional


class Monitor(object):
    """
    Performs health checks in a separate thread once every interval seconds
    and updates the internal state. Other parts of the SDK only read this state
    and act accordingly.
    """

    name = "sentry.monitor"

    def __init__(self, interval=60):
        # type: (int) -> None
        self.interval = interval  # type: int
        self._healthy = True
        self._downsample_factor = 1  # type: int

        self._thread = None  # type: Optional[Thread]
        self._thread_lock = Lock()
        self._thread_for_pid = None  # type: Optional[int]
        self._running = True

    def _ensure_running(self):
        # type: () -> None
        if self._thread_for_pid == os.getpid() and self._thread is not None:
            return None

        with self._thread_lock:
            if self._thread_for_pid == os.getpid() and self._thread is not None:
                return None

            def _thread():
                # type: (...) -> None
                while self._running:
                    time.sleep(self.interval)
                    if self._running:
                        self.check_health()
                        self._set_downsample_factor()

            thread = Thread(name=self.name, target=_thread)
            thread.daemon = True
            thread.start()
            self._thread = thread
            self._thread_for_pid = os.getpid()

        return None

    def _is_transport_rate_limited(self):
        # type: () -> bool
        transport = (
            sentry_sdk.Hub.current.client and sentry_sdk.Hub.current.client.transport
        )
        if transport and hasattr(transport, "_is_rate_limited"):
            return transport._is_rate_limited()
        return False

    def _set_downsample_factor(self):
        # type: () -> None
        if self._healthy:
            self._downsample_factor = 1
        else:
            self._downsample_factor *= 2
            logger.debug("monitor health check negative, downsampling with a factor of %d", self._downsample_factor)

    def check_health(self):
        # type: () -> None
        """
        Perform the actual health checks,
        currently only checks if the transport is rate-limited.
        TODO: augment in the future with more checks.
        """
        if self._is_transport_rate_limited():
            self._healthy = False
        else:
            self._healthy = True

    def is_healthy(self):
        # type: () -> bool
        return self._healthy

    def downsample_factor(self):
        # type: () -> int
        return self._downsample_factor

    def kill(self):
        # type: () -> None
        self._running = False

    def __del__(self):
        # type: () -> None
        self.kill()
