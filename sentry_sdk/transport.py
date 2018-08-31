from __future__ import print_function

import atexit
import json
import time
import io
import urllib3
import logging
import threading
import certifi
import sys
import traceback
import gzip

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

    opts = {"num_pools": 2, "cert_reqs": "CERT_REQUIRED", "ca_certs": certifi.where()}

    if proxy:
        return urllib3.ProxyManager(proxy, **opts)
    else:
        return urllib3.PoolManager(**opts)


_PYTHON_SHUTTING_DOWN = False
_SHUTDOWN = object()
_retry = urllib3.util.Retry()


@atexit.register
def _learn_about_shutting_down():
    global _PYTHON_SHUTTING_DOWN
    _PYTHON_SHUTTING_DOWN = True


def send_event(pool, event, auth):
    body = io.BytesIO()
    with gzip.GzipFile(fileobj=body, mode="w") as f:
        f.write(json.dumps(event).encode("utf-8"))

    response = pool.request(
        "POST",
        str(auth.store_api_url),
        body=body.getvalue(),
        headers={
            "X-Sentry-Auth": str(auth.to_header()),
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
        },
    )

    try:
        if response.status == 429:
            return datetime.utcnow() + timedelta(
                seconds=_retry.get_retry_after(response)
            )

        if response.status >= 300 or response.status < 200:
            raise ValueError("Unexpected status code: %s" % response.status)
    finally:
        response.close()


def spawn_thread(transport):
    auth = transport.dsn.to_auth("sentry-python/%s" % VERSION)

    def thread():
        disabled_until = None

        # copy to local var in case transport._queue is set to None
        q = transport._queue

        while 1:
            try:
                item = q.get(timeout=0.1)
            except queue.Empty:
                if _SHUTDOWN:
                    break
                continue

            if item is _SHUTDOWN:
                q.task_done()
                break

            if disabled_until is not None:
                if datetime.utcnow() < disabled_until:
                    q.task_done()
                    continue
                disabled_until = None

            try:
                disabled_until = send_event(transport._pool, item, auth)
            except Exception:
                # XXX: use the logger
                print(traceback.format_exc(), file=sys.stderr)
            finally:
                q.task_done()

    t = threading.Thread(target=thread)
    t.start()


class Transport(object):
    def __init__(self, dsn, http_proxy=None, https_proxy=None):
        self.dsn = dsn
        self._queue = None
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
        if self._queue is not None:
            try:
                self._queue.put_nowait(_SHUTDOWN)
            except queue.Full:
                pass
            self._queue = None

    def drain_events(self, timeout):
        q = self._queue
        if q is not None:
            started = time.time()
            with q.all_tasks_done:
                while q.unfinished_tasks and (time.time() - started) < timeout:
                    q.all_tasks_done.wait(0.1)

    def __del__(self):
        self.close()
