import io
import urllib3

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from typing import Dict
    from typing import Optional

from sentry_sdk.utils import logger
from sentry_sdk.envelope import Envelope


class SpotlightClient(object):
    def __init__(self, url):
        # type: (str) -> None
        self.url = url
        self.http = urllib3.PoolManager()

    def capture_envelope(self, envelope):
        # type: (Envelope) -> None
        body = io.BytesIO()
        envelope.serialize_into(body)
        try:
            req = self.http.request(
                url=self.url,
                body=body.getvalue(),
                method="POST",
                headers={
                    "Content-Type": "application/x-sentry-envelope",
                },
            )
            req.close()
        except Exception as e:
            logger.exception(str(e))


def setup_spotlight(options):
    # type: (Dict[str, Any]) -> Optional[SpotlightClient]

    url = options.get("spotlight")

    if isinstance(url, str):
        pass
    elif url is True:
        url = "http://localhost:8969/stream"
    else:
        return None

    return SpotlightClient(url)
