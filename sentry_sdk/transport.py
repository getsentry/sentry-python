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
import gzip

from datetime import datetime, timedelta

from .consts import VERSION
from .utils import Dsn, get_logger
from .worker import BackgroundWorker
from .hub import _internal_exceptions

try:
    from urllib.request import getproxies
except ImportError:
    from urllib import getproxies


logger = get_logger("sentry_sdk.errors")


def _make_pool(parsed_dsn, http_proxy, https_proxy):
    proxy = https_proxy if parsed_dsn == "https" else http_proxy
    if not proxy:
        proxy = getproxies().get(parsed_dsn.scheme)

    opts = {"num_pools": 2, "cert_reqs": "CERT_REQUIRED", "ca_certs": certifi.where()}

    if proxy:
        return urllib3.ProxyManager(proxy, **opts)
    else:
        return urllib3.PoolManager(**opts)


@atexit.register
def _shutdown():
    from .hub import Hub

    main_client = Hub.main.client
    if main_client is not None:
        main_client.close()


class Transport(object):
    def __init__(self, options=None):
        self.options = options
        if options and options["dsn"]:
            self.parsed_dsn = Dsn(options["dsn"])
        else:
            self.parsed_dsn = None

    def capture_event(self, event):
        raise NotImplementedError()

    def shutdown(self):
        self.kill()

    def kill(self):
        pass

    def __del__(self):
        try:
            self.kill()
        except Exception:
            pass


class HttpTransport(Transport):
    def __init__(self, options):
        Transport.__init__(self, options)
        self._worker = BackgroundWorker(
            shutdown_timeout=options["shutdown_timeout"],
            shutdown_callback=options["shutdown_callback"],
        )
        self._auth = self.parsed_dsn.to_auth("sentry-python/%s" % VERSION)
        self._pool = _make_pool(
            self.parsed_dsn,
            http_proxy=options["http_proxy"],
            https_proxy=options["https_proxy"],
        )
        self._disabled_until = None
        self._retry = urllib3.util.Retry()

    def _send_event(self, event):
        if self._disabled_until is not None:
            if datetime.utcnow() < self._disabled_until:
                return
            self._disabled_until = None

        with _internal_exceptions():
            body = io.BytesIO()
            with gzip.GzipFile(fileobj=body, mode="w") as f:
                f.write(json.dumps(event).encode("utf-8"))

            logger.debug(
                "Sending %s event [%s] to %s project:%s"
                % (
                    event.get("level") or "error",
                    event["event_id"],
                    self.parsed_dsn.host,
                    self.parsed_dsn.project_id,
                )
            )
            response = self._pool.request(
                "POST",
                str(self._auth.store_api_url),
                body=body.getvalue(),
                headers={
                    "X-Sentry-Auth": str(self._auth.to_header()),
                    "Content-Type": "application/json",
                    "Content-Encoding": "gzip",
                },
            )

            try:
                if response.status == 429:
                    self._disabled_until = datetime.utcnow() + timedelta(
                        seconds=_retry.get_retry_after(response)
                    )
                    return

                elif response.status >= 300 or response.status < 200:
                    raise ValueError("Unexpected status code: %s" % response.status)
            finally:
                response.close()

            self._disabled_until = None

    def capture_event(self, event):
        self._worker.submit(lambda: self._send_event(event))

    def shutdown(self):
        logger.debug("Shutting down HTTP transport orderly")
        self._worker.shutdown()

    def kill(self):
        logger.debug("Killing HTTP transport")
        self._worker.kill()


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
