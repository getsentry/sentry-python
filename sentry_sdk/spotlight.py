import io
from urllib import request

from sentry_sdk.utils import logger
from sentry_sdk.envelope import Envelope, Item, PayloadRef

class SpotlightSidecar(object):
    def __init__(self, port):
        self.port = port

    def capture_envelope(
        self, envelope  # type: Envelope
    ):
        body = io.BytesIO()
        envelope.serialize_into(body)

        req = request.Request(
            f"http://localhost:{self.port}/stream",
            data=body
            headers={
                'Content-Type': 'application/json',
            },
        )
        
        try:
            request.urlopen(req)
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