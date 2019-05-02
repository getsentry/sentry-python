import sys

from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception
from sentry_sdk._compat import PY2, reraise
from sentry_sdk.tracing import SpanContext
from sentry_sdk.integrations._wsgi_common import _filter_headers


if PY2:

    def wsgi_decoding_dance(s, charset="utf-8", errors="replace"):

        return s.decode(charset, errors)


else:

    def wsgi_decoding_dance(s, charset="utf-8", errors="replace"):

        return s.encode("latin1").decode(charset, errors)


def get_host(environ):

    """Return the host for the given WSGI environment. Yanked from Werkzeug."""
    if environ.get("HTTP_HOST"):
        rv = environ["HTTP_HOST"]
        if environ["wsgi.url_scheme"] == "http" and rv.endswith(":80"):
            rv = rv[:-3]
        elif environ["wsgi.url_scheme"] == "https" and rv.endswith(":443"):
            rv = rv[:-4]
    elif environ.get("SERVER_NAME"):
        rv = environ["SERVER_NAME"]
        if (environ["wsgi.url_scheme"], environ["SERVER_PORT"]) not in (
            ("https", "443"),
            ("http", "80"),
        ):
            rv += ":" + environ["SERVER_PORT"]
    else:
        # In spite of the WSGI spec, SERVER_NAME might not be present.
        rv = "unknown"

    return rv


def get_request_url(environ):

    """Return the absolute URL without query string for the given WSGI
    environment."""
    return "%s://%s/%s" % (
        environ.get("wsgi.url_scheme"),
        get_host(environ),
        wsgi_decoding_dance(environ.get("PATH_INFO") or "").lstrip("/"),
    )


class SentryWsgiMiddleware(object):
    __slots__ = ("app",)

    def __init__(self, app):

        self.app = app

    def __call__(self, environ, start_response):

        hub = Hub(Hub.current)

        with hub:
            with capture_internal_exceptions():
                with hub.configure_scope() as scope:
                    scope.clear_breadcrumbs()
                    scope._name = "wsgi"
                    scope.set_span_context(SpanContext.continue_from_environ(environ))
                    scope.add_event_processor(_make_wsgi_event_processor(environ))

            try:
                rv = self.app(environ, start_response)
            except Exception:
                reraise(*_capture_exception(hub))

        return _ScopedResponse(hub, rv)


def _get_environ(environ):

    """
    Returns our whitelisted environment variables.
    """
    keys = ["SERVER_NAME", "SERVER_PORT"]
    if _should_send_default_pii():
        # Add all three headers here to make debugging of proxy setup easier.
        keys += ["REMOTE_ADDR", "HTTP_X_FORWARDED_FOR", "HTTP_X_REAL_IP"]

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
    Infer the user IP address from various headers. This cannot be used in
    security sensitive situations since the value may be forged from a client,
    but it's good enough for the event payload.
    """
    try:
        return environ["HTTP_X_FORWARDED_FOR"].split(",")[0].strip()
    except (KeyError, IndexError):
        pass

    try:
        return environ["HTTP_X_REAL_IP"]
    except KeyError:
        pass

    return environ.get("REMOTE_ADDR")


def _capture_exception(hub):

    # Check client here as it might have been unset while streaming response
    if hub.client is not None:
        exc_info = sys.exc_info()
        event, hint = event_from_exception(
            exc_info,
            client_options=hub.client.options,
            mechanism={"type": "wsgi", "handled": False},
        )
        hub.capture_event(event, hint=hint)
    return exc_info


class _ScopedResponse(object):
    __slots__ = ("_response", "_hub")

    def __init__(self, hub, response):

        self._hub = hub
        self._response = response

    def __iter__(self):

        iterator = iter(self._response)

        while True:
            with self._hub:
                try:
                    chunk = next(iterator)
                except StopIteration:
                    break
                except Exception:
                    reraise(*_capture_exception(self._hub))

            yield chunk

    def close(self):
        with self._hub:
            try:
                self._response.close()
            except AttributeError:
                pass
            except Exception:
                reraise(*_capture_exception(self._hub))


def _make_wsgi_event_processor(environ):

    # It's a bit unfortunate that we have to extract and parse the request data
    # from the environ so eagerly, but there are a few good reasons for this.
    #
    # We might be in a situation where the scope/hub never gets torn down
    # properly. In that case we will have an unnecessary strong reference to
    # all objects in the environ (some of which may take a lot of memory) when
    # we're really just interested in a few of them.
    #
    # Keeping the environment around for longer than the request lifecycle is
    # also not necessarily something uWSGI can deal with:
    # https://github.com/unbit/uwsgi/issues/1950

    client_ip = get_client_ip(environ)
    request_url = get_request_url(environ)
    query_string = environ.get("QUERY_STRING")
    method = environ.get("REQUEST_METHOD")
    env = dict(_get_environ(environ))
    headers = _filter_headers(dict(_get_headers(environ)))

    def event_processor(event, hint):

        with capture_internal_exceptions():
            # if the code below fails halfway through we at least have some data
            request_info = event.setdefault("request", {})

            if _should_send_default_pii():
                user_info = event.setdefault("user", {})
                user_info["ip_address"] = client_ip

            request_info["url"] = request_url
            request_info["query_string"] = query_string
            request_info["method"] = method
            request_info["env"] = env
            request_info["headers"] = headers

        return event

    return event_processor
