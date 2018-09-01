from __future__ import absolute_import

from sentry_sdk import add_breadcrumb
from . import Integration

from .logging import ignore_logger


class RequestsIntegration(Integration):
    identifier = "requests"

    def __init__(self):
        from requests.sessions import Session

        self.session_cls = Session

    def install(self):
        real_send = self.session_cls.send

        def send(self, request, *args, **kwargs):
            def _record_request(response):
                add_breadcrumb(
                    type="http",
                    category="requests",
                    data={
                        "url": request.url,
                        "method": request.method,
                        "status_code": response is not None
                        and response.status_code
                        or None,
                        "reason": response is not None and response.reason or None,
                    },
                )

            try:
                resp = real_send(self, request, *args, **kwargs)
            except Exception:
                _record_request(None)
                raise
            else:
                print("here", resp)
                _record_request(resp)
            return resp

        self.session_cls.send = send
        ignore_logger("requests.packages.urllib3.connectionpool")
