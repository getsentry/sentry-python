import io
import urllib3

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from urllib3.poolmanager import PoolManager

from sentry_sdk.utils import logger
from sentry_sdk.envelope import Envelope

class SpotlightSidecar(object):
    def __init__(self, url):
        self.url = url
        self.http = urllib3.PoolManager()

    def capture_envelope(
        self, envelope  # type: Envelope
    ):
        body = io.BytesIO()
        envelope.serialize_into(body)

        try:
            req = self.http.request(
                url=self.url,
                body=body.getvalue(),
                method='POST',
                headers={
                    'Content-Type': 'application/x-sentry-envelope',
                },
            )
            req.close()
        except Exception as e:
            logger.exception(str(e))

instance = None

def setup_spotlight(options):
    global instance

    if instance is None:
        url = options["spotlight"]
        if isinstance(url, str):
            pass
        elif url is True:
            url = "http://localhost:8969/stream"
        else:
            return None
        
        instance = SpotlightSidecar(url)
    
    return instance


if __name__ == '__main__':
    from sentry_sdk.debug import configure_logger
    configure_logger()

    spotlight = setup_spotlight({})
    spotlight.wait()