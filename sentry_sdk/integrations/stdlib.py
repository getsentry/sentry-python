from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import add_global_event_processor
from sentry_sdk._compat import PY2

import sys
import platform

if PY2:
    from httplib import HTTPConnection
else:
    from http.client import HTTPConnection

_RUNTIME_CONTEXT = {
    "name": platform.python_implementation(),
    "version": "%s.%s.%s" % (sys.version_info[:3]),
    "build": sys.version,
}


class StdlibIntegration(Integration):
    identifier = "stdlib"

    @staticmethod
    def setup_once():
        # type: () -> None
        install_httplib()

        @add_global_event_processor
        def add_python_runtime_context(event, hint):
            if Hub.current.get_integration(StdlibIntegration) is not None:
                contexts = event.setdefault("contexts", {})
                if isinstance(contexts, dict) and "runtime" not in contexts:
                    contexts["runtime"] = _RUNTIME_CONTEXT

            return event


def install_httplib():
    # type: () -> None
    real_putrequest = HTTPConnection.putrequest
    real_getresponse = HTTPConnection.getresponse

    def putrequest(self, method, url, *args, **kwargs):
        rv = real_putrequest(self, method, url, *args, **kwargs)
        hub = Hub.current
        if hub.get_integration(StdlibIntegration) is None:
            return rv

        self._sentrysdk_data_dict = data = {}

        host = self.host
        port = self.port
        default_port = self.default_port

        real_url = url
        if not real_url.startswith(("http://", "https://")):
            real_url = "%s://%s%s%s" % (
                default_port == 443 and "https" or "http",
                host,
                port != default_port and ":%s" % port or "",
                url,
            )

        for key, value in hub.iter_trace_propagation_headers():
            self.putheader(key, value)

        data["url"] = real_url
        data["method"] = method
        return rv

    def getresponse(self, *args, **kwargs):
        rv = real_getresponse(self, *args, **kwargs)  # type: ignore
        hub = Hub.current
        if hub.get_integration(StdlibIntegration) is None:
            return rv

        data = getattr(self, "_sentrysdk_data_dict", None) or {}

        if "status_code" not in data:
            data["status_code"] = rv.status
            data["reason"] = rv.reason
        hub.add_breadcrumb(
            type="http", category="httplib", data=data, hint={"httplib_response": rv}
        )
        return rv

    HTTPConnection.putrequest = putrequest  # type: ignore
    HTTPConnection.getresponse = getresponse  # type: ignore
