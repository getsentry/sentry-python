import base64

from sentry_sdk.stripping import AnnotatedValue

def get_environ(environ):
    """
    Returns our whitelisted environment variables.
    """
    for key in ("REMOTE_ADDR", "SERVER_NAME", "SERVER_PORT"):
        if key in environ:
            yield key, environ[key]


# `get_headers` comes from `werkzeug.datastructures.EnvironHeaders`
def get_headers(environ):
    """
    Returns only proper HTTP headers.
    """
    for key, value in environ.items():
        key = str(key)
        if key.startswith('HTTP_') and key not in \
           ('HTTP_CONTENT_TYPE', 'HTTP_CONTENT_LENGTH'):
            yield key[5:].replace('_', '-').title(), value
        elif key in ('CONTENT_TYPE', 'CONTENT_LENGTH'):
            yield key.replace('_', '-').title(), value


class RequestExtractor(object):
    def __init__(self, request):
        self.request = request

    def extract_into_scope(self, scope):
        # if the code below fails halfway through we at least have some data
        scope.request = request_info = {}

        request_info['url'] = self.url
        request_info["query_string"] = self.query_string
        request_info["method"] = self.method
        request_info["headers"] = dict(self.headers)
        request_info["env"] = dict(get_environ(self.env))
        request_info["cookies"] = dict(self.cookies)

        if self.form or self.files:
            data = dict(self.form.items())
            for k, v in self.files.items():
                data[k] = AnnotatedValue(
                    "",
                    {
                        "len": self.size_of_file(v),
                        "rem": [["!filecontent", "x", 0, 0]]
                    }
                )

            if self.files or self.form_is_multipart:
                ct = "multipart"
            else:
                ct = "urlencoded"
            repr = "structured"
        elif self.json is not None:
            data = self.json
            ct = "json"
            repr = "structured"
        else:
            data = self.raw_data

            try:
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                ct = "plain"
                repr = "other"
            except UnicodeDecodeError:
                ct = "bytes"
                repr = "base64"
                data = base64.b64encode(data).decode("ascii")

        request_info["data"] = data
        request_info["data_info"] = {"ct": ct, "repr": repr}

    @property
    def url(self):
        raise NotImplementedError()

    @property
    def query_string(self):
        raise NotImplementedError()

    @property
    def method(self):
        raise NotImplementedError()

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
        raise NotImplementedError()

    @property
    def json(self):
        raise NotImplementedError()

    @property
    def files(self):
        raise NotImplementedError()

    def size_of_file(self, file):
        raise NotImplementedError()
