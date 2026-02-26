import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA, SPANSTATUS
from sentry_sdk.integrations import Integration, DidNotEnable, _check_minimum_version
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import capture_internal_exceptions, ensure_integration_enabled

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional, Tuple

try:
    from elasticsearch import VERSION  # type: ignore[import-not-found]
except ImportError:
    raise DidNotEnable("elasticsearch is not installed")


class ElasticsearchIntegration(Integration):
    identifier = "elasticsearch"
    origin = "auto.db.elasticsearch"

    @staticmethod
    def setup_once():
        # type: () -> None
        _check_minimum_version(ElasticsearchIntegration, VERSION)

        major_version = VERSION[0]

        if major_version >= 8:
            # v8/v9: perform_request is on the Elasticsearch client class
            from elasticsearch import Elasticsearch

            _patch_perform_request(Elasticsearch, major_version)

            try:
                from elasticsearch._async.client import AsyncElasticsearch

                _patch_perform_request_async(AsyncElasticsearch, major_version)
            except ImportError:
                pass
        else:
            # v7: perform_request is on the Transport class
            from elasticsearch import Transport

            _patch_perform_request(Transport, major_version)

            try:
                from elasticsearch._async.transport import AsyncTransport

                _patch_perform_request_async(AsyncTransport, major_version)
            except ImportError:
                pass


def _parse_url(url):
    # type: (str) -> Tuple[Optional[str], Optional[str]]
    """Extract the operation name and index from an Elasticsearch URL path.

    Returns a (operation, index) tuple.

    Examples:
        /my-index/_search  -> ("search", "my-index")
        /_search           -> ("search", None)
        /my-index/_doc/1   -> ("doc", "my-index")
        /_bulk             -> ("bulk", None)
        /                  -> (None, None)
    """
    parts = [p for p in url.split("/") if p]
    if not parts:
        return None, None

    operation = None
    index = None

    for i, part in enumerate(parts):
        if part.startswith("_"):
            operation = part.lstrip("_")
            if i > 0:
                index = parts[0]
            break

    if operation is None and parts:
        index = parts[0]

    return operation, index


def _get_connection_info(obj, major_version):
    # type: (Any, int) -> Tuple[Optional[str], Optional[int]]
    """Best-effort extraction of server address and port."""
    # v7: Transport has a hosts list of dicts
    try:
        host_info = obj.hosts[0]
        return host_info.get("host"), host_info.get("port")
    except Exception:
        pass

    # v8/v9: Elasticsearch client with node pool
    try:
        node = list(obj.transport.node_pool.all())[0]
        return node.config.host, node.config.port
    except Exception:
        pass

    return None, None


def _get_body(args, kwargs):
    # type: (Any, Any) -> Any
    """Extract the request body from args/kwargs.

    In v7, body can be positional (params=args[0], body=args[1]).
    In v8/v9, body is always a keyword argument.
    """
    body = kwargs.get("body")
    if body is None and len(args) > 1:
        body = args[1]
    return body


def _patch_perform_request(cls, major_version):
    # type: (Any, int) -> None
    original = cls.perform_request

    @ensure_integration_enabled(ElasticsearchIntegration, original)
    def _sentry_perform_request(self, method, path, *args, **kwargs):
        # type: (Any, str, str, *Any, **Any) -> Any
        operation, index = _parse_url(path)
        description = "{} {}".format(method, path)

        span = sentry_sdk.start_span(
            op=OP.DB,
            name=description,
            origin=ElasticsearchIntegration.origin,
        )

        span.set_data(SPANDATA.DB_SYSTEM, "elasticsearch")
        if operation:
            span.set_data(SPANDATA.DB_OPERATION, operation)
        if index:
            span.set_data(SPANDATA.DB_NAME, index)

        with capture_internal_exceptions():
            address, port = _get_connection_info(self, major_version)
            if address:
                span.set_data(SPANDATA.SERVER_ADDRESS, address)
            if port:
                span.set_data(SPANDATA.SERVER_PORT, port)

        if should_send_default_pii():
            body = _get_body(args, kwargs)
            if body is not None:
                span.set_data("db.statement.body", body)

        try:
            result = original(self, method, path, *args, **kwargs)
            span.set_status(SPANSTATUS.OK)
        except Exception:
            span.set_status(SPANSTATUS.INTERNAL_ERROR)
            raise
        finally:
            with capture_internal_exceptions():
                breadcrumb_data = {SPANDATA.DB_SYSTEM: "elasticsearch"}
                if operation:
                    breadcrumb_data[SPANDATA.DB_OPERATION] = operation
                if index:
                    breadcrumb_data[SPANDATA.DB_NAME] = index
                sentry_sdk.add_breadcrumb(
                    message=description,
                    category="query",
                    data=breadcrumb_data,
                )
            span.finish()

        return result

    cls.perform_request = _sentry_perform_request


def _patch_perform_request_async(cls, major_version):
    # type: (Any, int) -> None
    original = cls.perform_request

    async def _sentry_perform_request(self, method, path, *args, **kwargs):
        # type: (Any, str, str, *Any, **Any) -> Any
        if sentry_sdk.get_client().get_integration(ElasticsearchIntegration) is None:
            return await original(self, method, path, *args, **kwargs)

        operation, index = _parse_url(path)
        description = "{} {}".format(method, path)

        span = sentry_sdk.start_span(
            op=OP.DB,
            name=description,
            origin=ElasticsearchIntegration.origin,
        )

        span.set_data(SPANDATA.DB_SYSTEM, "elasticsearch")
        if operation:
            span.set_data(SPANDATA.DB_OPERATION, operation)
        if index:
            span.set_data(SPANDATA.DB_NAME, index)

        with capture_internal_exceptions():
            address, port = _get_connection_info(self, major_version)
            if address:
                span.set_data(SPANDATA.SERVER_ADDRESS, address)
            if port:
                span.set_data(SPANDATA.SERVER_PORT, port)

        if should_send_default_pii():
            body = _get_body(args, kwargs)
            if body is not None:
                span.set_data("db.statement.body", body)

        try:
            result = await original(self, method, path, *args, **kwargs)
            span.set_status(SPANSTATUS.OK)
        except Exception:
            span.set_status(SPANSTATUS.INTERNAL_ERROR)
            raise
        finally:
            with capture_internal_exceptions():
                breadcrumb_data = {SPANDATA.DB_SYSTEM: "elasticsearch"}
                if operation:
                    breadcrumb_data[SPANDATA.DB_OPERATION] = operation
                if index:
                    breadcrumb_data[SPANDATA.DB_NAME] = index
                sentry_sdk.add_breadcrumb(
                    message=description,
                    category="query",
                    data=breadcrumb_data,
                )
            span.finish()

        return result

    cls.perform_request = _sentry_perform_request
