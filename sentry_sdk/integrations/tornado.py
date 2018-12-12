import sys
import weakref

from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.utils import (
    event_from_exception,
    capture_internal_exceptions,
    transaction_from_function,
)
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations._wsgi_common import (
    RequestExtractor,
    _filter_headers,
    _is_json_content_type,
)
from sentry_sdk.integrations.logging import ignore_logger

from tornado.web import RequestHandler, HTTPError
from tornado.gen import coroutine


class TornadoIntegration(Integration):
    identifier = "tornado"

    @staticmethod
    def setup_once():
        import tornado

        tornado_version = getattr(tornado, "version_info", None)
        if tornado_version is None or tornado_version < (5, 0):
            raise RuntimeError("Tornado 5+ required")

        if sys.version_info < (3, 7):
            # Tornado is async. We better have contextvars or we're going to leak
            # state between requests.
            raise RuntimeError(
                "The tornado integration for Sentry requires Python 3.7+"
            )

        ignore_logger("tornado.application")
        ignore_logger("tornado.access")

        old_execute = RequestHandler._execute

        @coroutine
        def sentry_execute_request_handler(self, *args, **kwargs):
            hub = Hub.current
            integration = hub.get_integration(TornadoIntegration)
            if integration is None:
                return old_execute(self, *args, **kwargs)

            weak_handler = weakref.ref(self)

            with Hub(hub) as hub:
                with hub.configure_scope() as scope:
                    scope.add_event_processor(_make_event_processor(weak_handler))
                result = yield from old_execute(self, *args, **kwargs)
                return result

        RequestHandler._execute = sentry_execute_request_handler

        old_log_exception = RequestHandler.log_exception

        def sentry_log_exception(self, ty, value, tb, *args, **kwargs):
            _capture_exception(ty, value, tb)
            return old_log_exception(self, ty, value, tb, *args, **kwargs)

        RequestHandler.log_exception = sentry_log_exception


def _capture_exception(ty, value, tb):
    hub = Hub.current
    if hub.get_integration(TornadoIntegration) is None:
        return
    if isinstance(value, HTTPError):
        return

    event, hint = event_from_exception(
        (ty, value, tb),
        client_options=hub.client.options,
        mechanism={"type": "tornado", "handled": False},
    )

    hub.capture_event(event, hint=hint)


def _make_event_processor(weak_handler):
    def tornado_processor(event, hint):
        handler = weak_handler()
        if handler is None:
            return event

        request = handler.request

        if "transaction" not in event:
            with capture_internal_exceptions():
                method = getattr(handler, handler.request.method.lower())
                event["transaction"] = transaction_from_function(method)

        with capture_internal_exceptions():
            extractor = TornadoRequestExtractor(request)
            extractor.extract_into_event(event)

            request_info = event["request"]

            if "url" not in request_info:
                request_info["url"] = "%s://%s%s" % (
                    request.protocol,
                    request.host,
                    request.path,
                )

            if "query_string" not in request_info:
                request_info["query_string"] = request.query

            if "method" not in request_info:
                request_info["method"] = request.method

            if "env" not in request_info:
                request_info["env"] = {"REMOTE_ADDR": request.remote_ip}

            if "headers" not in request_info:
                request_info["headers"] = _filter_headers(dict(request.headers))

        with capture_internal_exceptions():
            if handler.current_user and _should_send_default_pii():
                event.setdefault("user", {})["is_authenticated"] = True

        return event

    return tornado_processor


class TornadoRequestExtractor(RequestExtractor):
    def content_length(self):
        if self.request.body is None:
            return 0
        return len(self.request.body)

    def cookies(self):
        return dict(self.request.cookies)

    def raw_data(self):
        return self.request.body

    def form(self):
        # TODO: Where to get formdata and nothing else?
        return None

    def is_json(self):
        return _is_json_content_type(self.request.headers.get("content-type"))

    def files(self):
        return {k: v[0] for k, v in self.request.files.items() if v}

    def size_of_file(self, file):
        return len(file.body or ())
