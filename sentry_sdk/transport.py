from __future__ import print_function

import json
import io
import urllib3
import threading
import certifi
import sys
import traceback
import gzip

from datetime import datetime, timedelta

from ._compat import queue
from .consts import VERSION
from .utils import Dsn

try:
    from urllib.request import getproxies
except ImportError:
    from urllib import getproxies


def _make_pool(parsed_dsn, http_proxy, https_proxy):
    proxy = https_proxy if parsed_dsn == "https" else http_proxy
    if not proxy:
        proxy = getproxies().get(parsed_dsn.scheme)

    opts = {"num_pools": 2, "cert_reqs": "CERT_REQUIRED", "ca_certs": certifi.where()}

    if proxy:
        return urllib3.ProxyManager(proxy, **opts)
    else:
        return urllib3.PoolManager(**opts)


_SHUTDOWN = object()
_retry = urllib3.util.Retry()


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
    auth = transport.parsed_dsn.to_auth("sentry-python/%s" % VERSION)

    def thread():
        disabled_until = None

        # copy to local var in case transport._queue is set to None
        queue = transport._queue

        while 1:
            item = queue.get()
            if item is _SHUTDOWN:
                queue.task_done()
                break

            if disabled_until is not None:
                if datetime.utcnow() < disabled_until:
                    queue.task_done()
                    continue
                disabled_until = None

            try:
                disabled_until = send_event(transport._pool, item, auth)
            except Exception:
                print("Could not send sentry event", file=sys.stderr)
                print(traceback.format_exc(), file=sys.stderr)
            finally:
                queue.task_done()

    t = threading.Thread(target=thread)
    t.setDaemon(True)
    t.start()


class Transport(object):
    def __init__(self, options=None):
        self.options = options
        if options and options["dsn"]:
            self.parsed_dsn = Dsn(options["dsn"])
        else:
            self.parsed_dsn = None

    def capture_event(self, event):
        raise NotImplementedError()

    def close(self):
        pass

    def drain_events(self, timeout):
        pass

    def __del__(self):
        self.close()


class HttpTransport(Transport):
    def __init__(self, options):
        Transport.__init__(self, options)
        self._queue = None
        self._pool = _make_pool(
            self.parsed_dsn,
            http_proxy=options["http_proxy"],
            https_proxy=options["https_proxy"],
        )
        self.start()

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
            with q.all_tasks_done:
                while q.unfinished_tasks:
                    q.all_tasks_done.wait(timeout)

    def __del__(self):
        self.close()


class _FunctionTransport(Transport):
    def __init__(self, func):
        Transport.__init__(self)
        self._func = func

    def capture_event(self, event):
        self._func(event)


def make_transport(options):
    ref_transport = options["transport"]

    # If no transport is given, we use the http transport class
    if ref_transport is None:
        transport_cls = HttpTransport
    else:
        try:
            issubclass(ref_transport, type)
        except TypeError:
            # if we are not a class but we are a callable, assume a
            # function that acts as capture_event
            if callable(ref_transport):
                return _FunctionTransport(ref_transport)
            # otherwise assume an object fulfilling the transport contract
            return ref_transport
        transport_cls = ref_transport

    # if a transport class is given only instanciate it if the dsn is not
    # empty or None
    if options["dsn"]:
        return transport_cls(options)
