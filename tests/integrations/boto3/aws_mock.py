from io import BytesIO
from botocore.awsrequest import AWSResponse


class Body(BytesIO):
    def stream(self, **kwargs):
        contents = self.read()
        while contents:
            yield contents
            contents = self.read()


class MockResponse(object):
    def __init__(self, client, status_code, headers, body):
        self._client = client
        self._status_code = status_code
        self._headers = headers
        self._body = body

    def __enter__(self):
        self._client.meta.events.register("before-send", self)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._client.meta.events.unregister("before-send", self)

    def __call__(self, request, **kwargs):
        return AWSResponse(
            request.url,
            self._status_code,
            self._headers,
            Body(self._body),
        )
