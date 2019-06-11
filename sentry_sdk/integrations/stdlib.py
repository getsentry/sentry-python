from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration
from sentry_sdk.tracing import record_http_request


try:
    from httplib import HTTPConnection  # type: ignore
except ImportError:
    from http.client import HTTPConnection


class StdlibIntegration(Integration):
    identifier = "stdlib"

    @staticmethod
    def setup_once():
        # type: () -> None
        install_httplib()


def install_httplib():
    # type: () -> None
    real_putrequest = HTTPConnection.putrequest
    real_getresponse = HTTPConnection.getresponse

    def putrequest(self, method, url, *args, **kwargs):
        hub = Hub.current
        if hub.get_integration(StdlibIntegration) is None:
            return real_putrequest(self, method, url, *args, **kwargs)

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

        self._sentrysdk_recorder = record_http_request(hub, real_url, method)
        self._sentrysdk_data_dict = self._sentrysdk_recorder.__enter__()

        try:
            rv = real_putrequest(self, method, url, *args, **kwargs)

            for key, value in hub.iter_trace_propagation_headers():
                self.putheader(key, value)
        except Exception:
            self._sentrysdk_recorder.__exit__(*sys.exc_info())
            self._sentrysdk_recorder = self._sentrysdk_data_dict = None
            raise

        return rv

    def getresponse(self, *args, **kwargs):
        recorder = getattr(self, "_sentrysdk_recorder", None)
        data_dict = getattr(self, "_sentrysdk_data_dict", None)

        try:
            rv = real_getresponse(self, *args, **kwargs)

            if recorder is not None and data_dict is not None:
                data_dict["httplib_response"] = rv
                data_dict["status_code"] = rv.status
                data_dict["reason"] = rv.reason
        finally:
            if recorder is not None:
                recorder.__exit__(*sys.exc_info())
            self._sentrysdk_recorder = self._sentrysdk_data_dict = None

        return rv

    HTTPConnection.putrequest = putrequest
    HTTPConnection.getresponse = getresponse
