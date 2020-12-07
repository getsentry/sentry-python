from __future__ import print_function

import io
import urllib3  # type: ignore
import certifi
import gzip

from datetime import datetime, timedelta

from sentry_sdk.utils import Dsn, logger, capture_internal_exceptions, json_dumps
from sentry_sdk.worker import BackgroundWorker
from sentry_sdk.envelope import Envelope

from sentry_sdk._types import MYPY

if MYPY:
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import Iterable
    from typing import Optional
    from typing import Tuple
    from typing import Type
    from typing import Union

    from urllib3.poolmanager import PoolManager  # type: ignore
    from urllib3.poolmanager import ProxyManager

    from sentry_sdk._types import Event, EndpointType

    DataCategory = Optional[str]

try:
    from urllib.request import getproxies
except ImportError:
    from urllib import getproxies  # type: ignore


class Transport(object):
    """Baseclass for all transports.

    A transport is used to send an event to sentry.
    """

    parsed_dsn = None  # type: Optional[Dsn]

    def __init__(
        self, options=None  # type: Optional[Dict[str, Any]]
    ):
        # type: (...) -> None
        self.options = options
        if options and options["dsn"] is not None and options["dsn"]:
            self.parsed_dsn = Dsn(options["dsn"])
        else:
            self.parsed_dsn = None

    def capture_event(
        self, event  # type: Event
    ):
        # type: (...) -> None
        """
        This gets invoked with the event dictionary when an event should
        be sent to sentry.
        """
        raise NotImplementedError()

    def capture_envelope(
        self, envelope  # type: Envelope
    ):
        # type: (...) -> None
        """
        Send an envelope to Sentry.

        Envelopes are a data container format that can hold any type of data
        submitted to Sentry. We use it for transactions and sessions, but
        regular "error" events should go through `capture_event` for backwards
        compat.
        """
        raise NotImplementedError()

    def flush(
        self,
        timeout,  # type: float
        callback=None,  # type: Optional[Any]
    ):
        # type: (...) -> None
        """Wait `timeout` seconds for the current events to be sent out."""
        pass

    def kill(self):
        # type: () -> None
        """Forcefully kills the transport."""
        pass

    def __del__(self):
        # type: () -> None
        try:
            self.kill()
        except Exception:
            pass


def _parse_rate_limits(header, now=None):
    # type: (Any, Optional[datetime]) -> Iterable[Tuple[DataCategory, datetime]]
    if now is None:
        now = datetime.utcnow()

    for limit in header.split(","):
        try:
            retry_after, categories, _ = limit.strip().split(":", 2)
            retry_after = now + timedelta(seconds=int(retry_after))
            for category in categories and categories.split(";") or (None,):
                yield category, retry_after
        except (LookupError, ValueError):
            continue


class HttpTransport(Transport):
    """The default HTTP transport."""

    def __init__(
        self, options  # type: Dict[str, Any]
    ):
        # type: (...) -> None
        from sentry_sdk.consts import VERSION

        Transport.__init__(self, options)
        assert self.parsed_dsn is not None
        self.options = options
        self._worker = BackgroundWorker(queue_size=options["transport_queue_size"])
        self._auth = self.parsed_dsn.to_auth("sentry.python/%s" % VERSION)
        self._disabled_until = {}  # type: Dict[DataCategory, datetime]
        self._retry = urllib3.util.Retry()

        self._pool = self._make_pool(
            self.parsed_dsn,
            http_proxy=options["http_proxy"],
            https_proxy=options["https_proxy"],
            ca_certs=options["ca_certs"],
        )

        from sentry_sdk import Hub

        self.hub_cls = Hub

    def _update_rate_limits(self, response):
        # type: (urllib3.HTTPResponse) -> None

        # new sentries with more rate limit insights.  We honor this header
        # no matter of the status code to update our internal rate limits.
        header = response.headers.get("x-sentry-rate-limits")
        if header:
            self._disabled_until.update(_parse_rate_limits(header))

        # old sentries only communicate global rate limit hits via the
        # retry-after header on 429.  This header can also be emitted on new
        # sentries if a proxy in front wants to globally slow things down.
        elif response.status == 429:
            self._disabled_until[None] = datetime.utcnow() + timedelta(
                seconds=self._retry.get_retry_after(response) or 60
            )

    def _send_request(
        self,
        body,  # type: bytes
        headers,  # type: Dict[str, str]
        endpoint_type="store",  # type: EndpointType
    ):
        # type: (...) -> None
        headers.update(
            {
                "User-Agent": str(self._auth.client),
                "X-Sentry-Auth": str(self._auth.to_header()),
            }
        )
        response = self._pool.request(
            "POST",
            str(self._auth.get_api_url(endpoint_type)),
            body=body,
            headers=headers,
        )

        try:
            self._update_rate_limits(response)

            if response.status == 429:
                # if we hit a 429.  Something was rate limited but we already
                # acted on this in `self._update_rate_limits`.
                pass

            elif response.status >= 300 or response.status < 200:
                logger.error(
                    "Unexpected status code: %s (body: %s)",
                    response.status,
                    response.data,
                )
        finally:
            response.close()

    def _check_disabled(self, category):
        # type: (str) -> bool
        def _disabled(bucket):
            # type: (Any) -> bool
            ts = self._disabled_until.get(bucket)
            return ts is not None and ts > datetime.utcnow()

        return _disabled(category) or _disabled(None)

    def _send_event(
        self, event  # type: Event
    ):
        # type: (...) -> None

        if self._check_disabled("error"):
            return None

        body = io.BytesIO()
        with gzip.GzipFile(fileobj=body, mode="w") as f:
            f.write(json_dumps(event))

        assert self.parsed_dsn is not None
        logger.debug(
            "Sending event, type:%s level:%s event_id:%s project:%s host:%s"
            % (
                event.get("type") or "null",
                event.get("level") or "null",
                event.get("event_id") or "null",
                self.parsed_dsn.project_id,
                self.parsed_dsn.host,
            )
        )
        self._send_request(
            body.getvalue(),
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        )
        return None

    def _send_envelope(
        self, envelope  # type: Envelope
    ):
        # type: (...) -> None

        # remove all items from the envelope which are over quota
        envelope.items[:] = [
            x for x in envelope.items if not self._check_disabled(x.data_category)
        ]
        if not envelope.items:
            return None

        body = io.BytesIO()
        with gzip.GzipFile(fileobj=body, mode="w") as f:
            envelope.serialize_into(f)

        assert self.parsed_dsn is not None
        logger.debug(
            "Sending envelope [%s] project:%s host:%s",
            envelope.description,
            self.parsed_dsn.project_id,
            self.parsed_dsn.host,
        )
        self._send_request(
            body.getvalue(),
            headers={
                "Content-Type": "application/x-sentry-envelope",
                "Content-Encoding": "gzip",
            },
            endpoint_type="envelope",
        )
        return None

    def _get_pool_options(self, ca_certs):
        # type: (Optional[Any]) -> Dict[str, Any]
        return {
            "num_pools": 2,
            "cert_reqs": "CERT_REQUIRED",
            "ca_certs": ca_certs or certifi.where(),
        }

    def _in_no_proxy(self, parsed_dsn):
        # type: (Dsn) -> bool
        no_proxy = getproxies().get("no")
        if not no_proxy:
            return False
        for host in no_proxy.split(","):
            host = host.strip()
            if parsed_dsn.host.endswith(host) or parsed_dsn.netloc.endswith(host):
                return True
        return False

    def _make_pool(
        self,
        parsed_dsn,  # type: Dsn
        http_proxy,  # type: Optional[str]
        https_proxy,  # type: Optional[str]
        ca_certs,  # type: Optional[Any]
    ):
        # type: (...) -> Union[PoolManager, ProxyManager]
        proxy = None
        no_proxy = self._in_no_proxy(parsed_dsn)

        # try HTTPS first
        if parsed_dsn.scheme == "https" and (https_proxy != ""):
            proxy = https_proxy or (not no_proxy and getproxies().get("https"))

        # maybe fallback to HTTP proxy
        if not proxy and (http_proxy != ""):
            proxy = http_proxy or (not no_proxy and getproxies().get("http"))

        opts = self._get_pool_options(ca_certs)

        if proxy:
            return urllib3.ProxyManager(proxy, **opts)
        else:
            return urllib3.PoolManager(**opts)

    def capture_event(
        self, event  # type: Event
    ):
        # type: (...) -> None
        hub = self.hub_cls.current

        def send_event_wrapper():
            # type: () -> None
            with hub:
                with capture_internal_exceptions():
                    self._send_event(event)

        self._worker.submit(send_event_wrapper)

    def capture_envelope(
        self, envelope  # type: Envelope
    ):
        # type: (...) -> None
        hub = self.hub_cls.current

        def send_envelope_wrapper():
            # type: () -> None
            with hub:
                with capture_internal_exceptions():
                    self._send_envelope(envelope)

        self._worker.submit(send_envelope_wrapper)

    def flush(
        self,
        timeout,  # type: float
        callback=None,  # type: Optional[Any]
    ):
        # type: (...) -> None
        logger.debug("Flushing HTTP transport")
        if timeout > 0:
            self._worker.flush(timeout, callback)

    def kill(self):
        # type: () -> None
        logger.debug("Killing HTTP transport")
        self._worker.kill()


class _FunctionTransport(Transport):
    def __init__(
        self, func  # type: Callable[[Event], None]
    ):
        # type: (...) -> None
        Transport.__init__(self)
        self._func = func

    def capture_event(
        self, event  # type: Event
    ):
        # type: (...) -> None
        self._func(event)
        return None


def make_transport(options):
    # type: (Dict[str, Any]) -> Optional[Transport]
    ref_transport = options["transport"]

    # If no transport is given, we use the http transport class
    if ref_transport is None:
        transport_cls = HttpTransport  # type: Type[Transport]
    elif isinstance(ref_transport, Transport):
        return ref_transport
    elif isinstance(ref_transport, type) and issubclass(ref_transport, Transport):
        transport_cls = ref_transport
    elif callable(ref_transport):
        return _FunctionTransport(ref_transport)  # type: ignore

    # if a transport class is given only instantiate it if the dsn is not
    # empty or None
    if options["dsn"]:
        return transport_cls(options)

    return None
