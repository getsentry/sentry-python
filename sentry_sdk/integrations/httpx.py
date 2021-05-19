from sentry_sdk import Hub
from sentry_sdk.integrations import Integration

try:
    from httpx import AsyncClient, Client, Request, Response
except ImportError:
    raise DidNotEnable("httpx is not installed")

__all__ = ["HttpxIntegration"]


class HttpxIntegration(Integration):
    identifier = "httpx"

    @staticmethod
    def setup_once():
        # type: () -> None
        _install_httpx_client()
        _install_httpx_async_client()


def _install_httpx_client():
    real_send = Client.send

    def send(self, request: Request, **kwargs) -> Response:
        hub = Hub.current
        if hub.get_integration(HttpxIntegration) is None:
            return real_send(self, request, **kwargs)

        with hub.start_span(
                op="http", description="%s %s" % (request.method, request.url)
        ) as span:
            span.set_data("method", request.method)
            span.set_data("url", str(request.url))
            for key, value in hub.iter_trace_propagation_headers():
                request.headers[key] = value
            rv = real_send(self, request, **kwargs)

            span.set_data("status_code", rv.status_code)
            span.set_http_status(rv.status_code)
            span.set_data("reason", rv.reason_phrase)
            return rv

    Client.send = send


def _install_httpx_async_client():
    real_send = AsyncClient.send

    async def send(self, request: Request, **kwargs) -> Response:
        hub = Hub.current
        if hub.get_integration(HttpxIntegration) is None:
            return await real_send(self, request, **kwargs)

        with hub.start_span(
                op="http", description="%s %s" % (request.method, request.url)
        ) as span:
            span.set_data("method", request.method)
            span.set_data("url", str(request.url))
            for key, value in hub.iter_trace_propagation_headers():
                request.headers[key] = value
            rv = await real_send(self, request, **kwargs)

            span.set_data("status_code", rv.status_code)
            span.set_http_status(rv.status_code)
            span.set_data("reason", rv.reason_phrase)
            return rv

    AsyncClient.send = send
