from sentry_sdk import add_breadcrumb
from sentry_sdk.integrations import Integration


class StdlibIntegration(Integration):
    identifier = "stdlib"

    def __init__(self):
        try:
            from httplib import HTTPConnection
        except ImportError:
            from http.client import HTTPConnection
        self.httplib_connection_cls = HTTPConnection

    def install_httplib(self):
        real_putrequest = self.httplib_connection_cls.putrequest
        real_getresponse = self.httplib_connection_cls.getresponse

        def putrequest(self, method, url, *args, **kwargs):
            rv = real_putrequest(self, method, url, *args, **kwargs)
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
            data = getattr(self, "_sentrysdk_data_dict", None) or {}

            if "status_code" not in data:
                data["status_code"] = rv.status
                data["reason"] = rv.reason
            add_breadcrumb(
                type="http",
                category="httplib",
                data=data,
                hint={"httplib_response": rv},
            )
            return rv

        self.httplib_connection_cls.putrequest = putrequest
        self.httplib_connection_cls.getresponse = getresponse

    def install(self):
        self.install_httplib()
