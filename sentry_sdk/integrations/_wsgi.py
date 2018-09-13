import json
import sys

from sentry_sdk import capture_exception
from sentry_sdk.hub import Hub, _should_send_default_pii, _get_client_options
from sentry_sdk.utils import AnnotatedValue, capture_internal_exceptions
from sentry_sdk._compat import reraise, implements_iterator


def get_environ(environ):
    """
    Returns our whitelisted environment variables.
    """
    for key in ("REMOTE_ADDR", "SERVER_NAME", "SERVER_PORT"):
        if key in environ:
            yield key, environ[key]


# `get_headers` comes from `werkzeug.datastructures.EnvironHeaders`
#
# We need this function because Django does not give us a "pure" http header
# dict. So we might as well use it for all WSGI integrations.
def get_headers(environ):
    """
    Returns only proper HTTP headers.

    """
    for key, value in environ.items():
        key = str(key)
        if key.startswith("HTTP_") and key not in (
            "HTTP_CONTENT_TYPE",
            "HTTP_CONTENT_LENGTH",
        ):
            yield key[5:].replace("_", "-").title(), value
        elif key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            yield key.replace("_", "-").title(), value


class RequestExtractor(object):
    def __init__(self, request):
        self.request = request

    def extract_into_event(self, event):
        client_options = _get_client_options()
        if client_options is None:
            return

        content_length = self.content_length()
        request_info = event.setdefault("request", {})
        request_info["url"] = self.url()

        if _should_send_default_pii():
            request_info["cookies"] = dict(self.cookies())

        bodies = client_options["request_bodies"]
        if (
            bodies == "never"
            or (bodies == "small" and content_length > 10 ** 3)
            or (bodies == "medium" and content_length > 10 ** 4)
        ):
            data = AnnotatedValue(
                "",
                {"rem": [["!config", "x", 0, content_length]], "len": content_length},
            )
        else:
            parsed_body = self.parsed_body()
            if parsed_body:
                data = parsed_body
            elif self.raw_data():
                data = AnnotatedValue(
                    "",
                    {"rem": [["!raw", "x", 0, content_length]], "len": content_length},
                )
            else:
                return

        request_info["data"] = data

    def content_length(self):
        try:
            return int(self.env().get("CONTENT_LENGTH", 0))
        except ValueError:
            return 0

    def url(self):
        raise NotImplementedError()

    def cookies(self):
        raise NotImplementedError()

    def raw_data(self):
        raise NotImplementedError()

    def form(self):
        raise NotImplementedError()

    def parsed_body(self):
        form = self.form()
        files = self.files()
        if form or files:
            data = dict(form.items())
            for k, v in files.items():
                size = self.size_of_file(v)
                data[k] = AnnotatedValue(
                    "", {"len": size, "rem": [["!raw", "x", 0, size]]}
                )

            return data

        return self.json()

    def is_json(self):
        mt = (self.env().get("CONTENT_TYPE") or "").split(";", 1)[0]
        return (
            mt == "application/json"
            or (mt.startswith("application/"))
            and mt.endswith("+json")
        )

    def json(self):
        try:
            if self.is_json():
                return json.loads(self.raw_data().decode("utf-8"))
        except ValueError:
            pass

    def files(self):
        raise NotImplementedError()

    def size_of_file(self, file):
        raise NotImplementedError()


def get_client_ip(environ):
    """
    Naively yank the first IP address in an X-Forwarded-For header
    and assume this is correct.

    Note: Don't use this in security sensitive situations since this
    value may be forged from a client.
    """
    try:
        return environ["HTTP_X_FORWARDED_FOR"].split(",")[0].strip()
    except (KeyError, IndexError):
        return environ.get("REMOTE_ADDR")


def run_wsgi_app(app, environ, start_response):
    hub = Hub.current
    hub.push_scope()
    with capture_internal_exceptions():
        with hub.configure_scope() as scope:
            scope.add_event_processor(_make_wsgi_event_processor(environ))

    try:
        rv = app(environ, start_response)
    except Exception:
        einfo = sys.exc_info()
        capture_exception(einfo)
        hub.pop_scope_unsafe()
        reraise(*einfo)

    return _ScopePoppingResponse(hub, rv)


@implements_iterator
class _ScopePoppingResponse(object):
    __slots__ = ("_response", "_hub")

    def __init__(self, hub, response):
        self._hub = hub
        self._response = response

    def __iter__(self):
        try:
            self._response = iter(self._response)
        except Exception:
            einfo = sys.exc_info()
            capture_exception(einfo)
            reraise(*einfo)
        return self

    def __next__(self):
        try:
            return next(self._response)
        except StopIteration:
            raise
        except Exception:
            einfo = sys.exc_info()
            capture_exception(einfo)
            reraise(*einfo)

    def close(self):
        self._hub.pop_scope_unsafe()
        if hasattr(self._response, "close"):
            try:
                self._response.close()
            except Exception:
                einfo = sys.exc_info()
                capture_exception(einfo)
                reraise(*einfo)


def _make_wsgi_event_processor(environ):
    def event_processor(event):
        with capture_internal_exceptions():
            # if the code below fails halfway through we at least have some data
            request_info = event.setdefault("request", {})

            if _should_send_default_pii():
                user_info = event.setdefault("user", {})
                user_info["ip_address"] = get_client_ip(environ)

            if "query_string" not in request_info:
                request_info["query_string"] = environ.get("QUERY_STRING")

            if "method" not in request_info:
                request_info["method"] = environ.get("REQUEST_METHOD")

            if "env" not in request_info:
                request_info["env"] = dict(get_environ(environ))

            if "headers" not in request_info:
                request_info["headers"] = dict(get_headers(environ))
                if not _should_send_default_pii():
                    request_info["headers"] = {
                        k: v
                        for k, v in request_info["headers"].items()
                        if k.lower().replace("_", "-")
                        not in ("set-cookie", "cookie", "authorization")
                    }

        return event

    return event_processor
