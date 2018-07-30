import json
import time
import zlib
import urllib3
import logging
import threading
import certifi
from datetime import datetime, timedelta

from ._compat import queue
from .consts import VERSION

try:
    from urllib.request import getproxies
except ImportError:
    from urllib import getproxies


logger = logging.getLogger(__name__)


def _make_pool(dsn, http_proxy, https_proxy):
    proxy = https_proxy if dsn == "https" else http_proxy
    if not proxy:
        proxy = getproxies().get(dsn.scheme)

    opts = {"num_pools": 2, "cert_reqs": "CERT_NONE", "ca_certs": certifi.where()}

    if proxy:
        return urllib3.ProxyManager(proxy, **opts)
    else:
        return urllib3.PoolManager(**opts)


_SHUTDOWN = object()
_retry = urllib3.util.Retry()


def send_event(pool, event, auth):
    body = zlib.compress(json.dumps(event).encode("utf-8"))
    response = pool.request(
        "POST",
        auth.store_api_url,
        body=body,
        headers={
            "X-Sentry-Auth": auth.to_header(),
            "Content-Type": "application/json",
            "Content-Encoding": "deflate",
        },
    )
    try:
        if response.status == 429:
            return datetime.utcnow() + timedelta(
                seconds=_retry.get_retry_after(response)
            )
    finally:
        response.close()


def spawn_thread(transport):
    auth = transport.dsn.to_auth("sentry-python/%s" % VERSION)

    def thread():
        disabled_until = None
        while 1:
            try:
                item = transport._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if disabled_until is not None:
                if disabled_until < datetime.utcnow():
                    continue
                disabled_until = None

            if item is None:
                if transport._done:
                    break
                continue
            elif item is _SHUTDOWN:
                break

            try:
                disabled_until = send_event(transport._pool, item, auth)
            except Exception:
                logger.exception("Could not send sentry event")
                continue

    t = threading.Thread(target=thread)
    t.setDaemon(True)
    t.start()


class Transport(object):
    def __init__(self, dsn, http_proxy=None, https_proxy=None):
        self.dsn = dsn
        self._queue = None
        self._done = False
        self._pool = _make_pool(dsn, http_proxy=http_proxy, https_proxy=https_proxy)

    def start(self):
        if self._queue is None:
            self._queue = queue.Queue(30)
            spawn_thread(self)

    def capture_event(self, event):
        if self._queue is None:
            raise RuntimeError("Transport shut down")
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            pass

    def close(self):
        self._done = True
        if self._queue is not None:
            try:
                self._queue.put_nowait(_SHUTDOWN)
            except queue.Full:
                pass
            self._queue = None

    def drain_events(self, timeout):
        deadline = time.time() + timeout
        q = self._queue
        while not self._done and q.qsize() > 0:
            if time.time() >= deadline:
                return False
            time.sleep(0.1)
        return True

    def __del__(self):
        self.close()
