import json

from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.utils import AnnotatedValue
from sentry_sdk._compat import text_type


class RequestExtractor(object):
    def __init__(self, request):
        self.request = request

    def extract_into_event(self, event):
        client = Hub.current.client
        if client is None:
            return

        content_length = self.content_length()
        request_info = event.setdefault("request", {})

        if _should_send_default_pii():
            request_info["cookies"] = dict(self.cookies())

        bodies = client.options["request_bodies"]
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
                raw_data = self.raw_data()
                if not isinstance(raw_data, text_type):
                    raw_data = raw_data.decode("utf-8")
                return json.loads(raw_data)
        except ValueError:
            pass

    def files(self):
        raise NotImplementedError()

    def size_of_file(self, file):
        raise NotImplementedError()


def _filter_headers(headers):
    if _should_send_default_pii():
        return headers

    return {
        k: v
        for k, v in headers.items()
        if k.lower().replace("_", "-") not in ("set-cookie", "cookie", "authorization")
    }
