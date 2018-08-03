import json

from sentry_sdk.hub import _should_send_default_pii
from sentry_sdk.event import AnnotatedValue


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

    def extract_into_event(self, event, client_options):
        if "request" in event:
            return

        # if the code below fails halfway through we at least have some data
        event["request"] = request_info = {}

        request_info["url"] = self.url
        request_info["query_string"] = self.query_string
        request_info["method"] = self.method

        request_info["env"] = dict(get_environ(self.env))

        if _should_send_default_pii():
            request_info["headers"] = dict(self.headers)
            request_info["cookies"] = dict(self.cookies)
        else:
            request_info["headers"] = {
                k: v
                for k, v in dict(self.headers).items()
                if k.lower().replace("_", "-")
                not in ("set-cookie", "cookie", "authentication")
            }

        bodies = client_options.get("request_bodies")
        if (
            bodies == "never"
            or (bodies == "small" and self.content_length > 10 ** 3)
            or (bodies == "medium" and self.content_length > 10 ** 4)
        ):
            data = AnnotatedValue(
                "",
                {
                    "rem": [["!config", "x", 0, self.content_length]],
                    "len": self.content_length,
                },
            )
        elif self.form or self.files:
            data = dict(self.form.items())
            for k, v in self.files.items():
                size = self.size_of_file(v)
                data[k] = AnnotatedValue(
                    "", {"len": size, "rem": [["!filecontent", "x", 0, size]]}
                )

        elif self.json is not None:
            data = self.json
        elif self.raw_data:
            data = AnnotatedValue(
                "",
                {
                    "rem": [["!rawbody", "x", 0, self.content_length]],
                    "len": self.content_length,
                },
            )
        else:
            return

        request_info["data"] = data

    @property
    def content_length(self):
        try:
            return int(self.env.get("CONTENT_LENGTH", 0))
        except ValueError:
            return 0

    @property
    def url(self):
        raise NotImplementedError()

    @property
    def query_string(self):
        return self.env.get("QUERY_STRING")

    @property
    def method(self):
        return self.env.get("REQUEST_METHOD")

    @property
    def headers(self):
        return get_headers(self.env)

    @property
    def env(self):
        raise NotImplementedError()

    @property
    def cookies(self):
        raise NotImplementedError()

    @property
    def raw_data(self):
        raise NotImplementedError()

    @property
    def form(self):
        raise NotImplementedError()

    @property
    def form_is_multipart(self):
        return self.env.get("CONTENT_TYPE").startswith("multipart/form-data")

    @property
    def is_json(self):
        mt = (self.env.get("CONTENT_TYPE") or "").split(";", 1)[0]
        return (
            mt == "application/json"
            or (mt.startswith("application/"))
            and mt.endswith("+json")
        )

    @property
    def json(self):
        try:
            if self.is_json:
                return json.loads(self.raw_data.decode("utf-8"))
        except ValueError:
            pass

    @property
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
