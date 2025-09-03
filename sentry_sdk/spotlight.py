from __future__ import annotations
import io
import logging
import os
import urllib.parse
import urllib.request
import urllib.error
import urllib3
import sys

from itertools import chain, product

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, Optional

from sentry_sdk.utils import (
    logger as sentry_logger,
    env_to_bool,
    capture_internal_exceptions,
)
from sentry_sdk.envelope import Envelope


logger = logging.getLogger("spotlight")


DEFAULT_SPOTLIGHT_URL = "http://localhost:8969/stream"
DJANGO_SPOTLIGHT_MIDDLEWARE_PATH = "sentry_sdk.spotlight.SpotlightMiddleware"


class SpotlightClient:
    def __init__(self, url: str) -> None:
        self.url = url
        self.http = urllib3.PoolManager()
        self.fails = 0

    def capture_envelope(self, envelope: Envelope) -> None:
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
            self.fails = 0
        except Exception as e:
            if self.fails < 2:
                sentry_logger.warning(str(e))
                self.fails += 1
            elif self.fails == 2:
                self.fails += 1
                sentry_logger.warning(
                    "Looks like Spotlight is not running, will keep trying to send events but will not log errors."
                )
            # omitting self.fails += 1 in the `else:` case intentionally
            # to avoid overflowing the variable if Spotlight never becomes reachable


def setup_spotlight(options: Dict[str, Any]) -> Optional[SpotlightClient]:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter(" [spotlight] %(levelname)s: %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

    url = options.get("spotlight")

    if url is True:
        url = DEFAULT_SPOTLIGHT_URL

    if not isinstance(url, str):
        return None

    client = SpotlightClient(url)
    logger.info("Enabled Spotlight using sidecar at %s", url)

    return client
