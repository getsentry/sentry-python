from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration


try:
    from httplib import HTTPConnection
except ImportError:
    from http.client import HTTPConnection


class StdlibIntegration(Integration):
    identifier = "stdlib"

    @staticmethod
    def setup_once():
        install_httplib()


def install_httplib():
    real_putrequest = HTTPConnection.putrequest
    real_getresponse = HTTPConnection.getresponse

    def putrequest(self, method, url, *args, **kwargs):
        rv = real_putrequest(self, method, url, *args, **kwargs)
        if Hub.current.get_integration(StdlibIntegration) is None:
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

        data["url"] = real_url
        data["method"] = method
        return rv

    def getresponse(self, *args, **kwargs):
        rv = real_getresponse(self, *args, **kwargs)
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

    HTTPConnection.putrequest = putrequest
    HTTPConnection.getresponse = getresponse
