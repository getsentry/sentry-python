import io
import urllib3

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from urllib3.poolmanager import PoolManager

from sentry_sdk.utils import logger
from sentry_sdk.envelope import Envelope

class SpotlightSidecar(object):
    def __init__(self, port):
        self.port = port

    def ensure(self):
        pass

    def capture_envelope(
        self, envelope  # type: Envelope
    ):
        body = io.BytesIO()
        envelope.serialize_into(body)

        http = urllib3.PoolManager()

        try:
            req = http.request(
                url=f"http://localhost:{self.port}/stream",
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
        instance = SpotlightSidecar(port=8969)
    
    instance.ensure()
    
    return instance


if __name__ == '__main__':
    from sentry_sdk.debug import configure_logger
    configure_logger()

    spotlight = setup_spotlight({})
    spotlight.wait()