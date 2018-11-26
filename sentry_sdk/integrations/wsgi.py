import sys

from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception
from sentry_sdk._compat import reraise, implements_iterator
from sentry_sdk.integrations._wsgi_common import _filter_headers


class SentryWsgiMiddleware(object):
    __slots__ = ("app",)

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        hub = Hub.current
        hub.push_scope()
        with capture_internal_exceptions():
            with hub.configure_scope() as scope:
                scope.add_event_processor(_make_wsgi_event_processor(environ))

        try:
            rv = self.app(environ, start_response)
        except Exception:
            einfo = _capture_exception(hub)
            hub.pop_scope_unsafe()
            reraise(*einfo)

        return _ScopePoppingResponse(hub, rv)


def _get_environ(environ):
    """
    Returns our whitelisted environment variables.
    """
    keys = ("SERVER_NAME", "SERVER_PORT")
    if _should_send_default_pii():
        keys += ("REMOTE_ADDR",)

    for key in keys:
        if key in environ:
            yield key, environ[key]


# `get_headers` comes from `werkzeug.datastructures.EnvironHeaders`
#
# We need this function because Django does not give us a "pure" http header
# dict. So we might as well use it for all WSGI integrations.
def _get_headers(environ):
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


def _capture_exception(hub):
    exc_info = sys.exc_info()
    event, hint = event_from_exception(
        exc_info,
        client_options=hub.client.options,
        mechanism={"type": "wsgi", "handled": False},
    )
    hub.capture_event(event, hint=hint)
    return exc_info


@implements_iterator
class _ScopePoppingResponse(object):
    __slots__ = ("_response", "_iterator", "_hub", "_popped")

    def __init__(self, hub, response):
        self._hub = hub
        self._response = response
        self._iterator = None
        self._popped = False

    def __iter__(self):
        try:
            self._iterator = iter(self._response)
        except Exception:
            reraise(*_capture_exception(self._hub))
        return self

    def __next__(self):
        if self._iterator is None:
            self.__iter__()

        try:
            return next(self._iterator)
        except StopIteration:
            raise
        except Exception:
            reraise(*_capture_exception(self._hub))

    def close(self):
        if not self._popped:
            self._hub.pop_scope_unsafe()
            self._popped = True

        try:
            self._response.close()
        except AttributeError:
            pass
        except Exception:
            reraise(*_capture_exception(self._hub))


def _make_wsgi_event_processor(environ):
    def event_processor(event, hint):
        with capture_internal_exceptions():
            # if the code below fails halfway through we at least have some data
            request_info = event.setdefault("request", {})

            if _should_send_default_pii():
                user_info = event.setdefault("user", {})
                if "ip_address" not in user_info:
                    user_info["ip_address"] = get_client_ip(environ)

            if "query_string" not in request_info:
                request_info["query_string"] = environ.get("QUERY_STRING")

            if "method" not in request_info:
                request_info["method"] = environ.get("REQUEST_METHOD")

            if "env" not in request_info:
                request_info["env"] = dict(_get_environ(environ))

            if "headers" not in request_info:
                request_info["headers"] = _filter_headers(dict(_get_headers(environ)))

        return event

    return event_processor
