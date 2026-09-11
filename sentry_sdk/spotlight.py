import io
import logging
import os
import sys
import time
from typing import TYPE_CHECKING

import urllib3

if TYPE_CHECKING:
    from typing import Any, Dict, Optional

from sentry_sdk.envelope import Envelope
from sentry_sdk.utils import (
    env_to_bool,
)
from sentry_sdk.utils import (
    logger as sentry_logger,
)

logger = logging.getLogger("spotlight")


DEFAULT_SPOTLIGHT_URL = "http://localhost:8969/stream"


class SpotlightClient:
    """
    A client for sending envelopes to Sentry Spotlight.

    Implements exponential backoff retry logic per the SDK spec:
    - Logs error at least once when server is unreachable
    - Does not log for every failed envelope
    - Uses exponential backoff to avoid hammering an unavailable server
    - Never blocks normal Sentry operation
    """

    # Exponential backoff settings
    INITIAL_RETRY_DELAY = 1.0  # Start with 1 second
    MAX_RETRY_DELAY = 60.0  # Max 60 seconds

    def __init__(self, url: str) -> None:
        self.url = url
        self.http = urllib3.PoolManager()
        self._retry_delay = self.INITIAL_RETRY_DELAY
        self._last_error_time: float = 0.0

    def capture_envelope(self, envelope: "Envelope") -> None:
        # Check if we're in backoff period - skip sending to avoid blocking
        if self._last_error_time > 0:
            time_since_error = time.time() - self._last_error_time
            if time_since_error < self._retry_delay:
                # Still in backoff period, skip this envelope
                return

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
            # Success - reset backoff state
            self._retry_delay = self.INITIAL_RETRY_DELAY
            self._last_error_time = 0.0
        except Exception as e:
            self._last_error_time = time.time()

            # Increase backoff delay exponentially first, so logged value matches actual wait
            self._retry_delay = min(self._retry_delay * 2, self.MAX_RETRY_DELAY)

            # Log error once per backoff cycle (we skip sends during backoff, so only one failure per cycle)
            sentry_logger.warning(
                "Failed to send envelope to Spotlight at %s: %s. "
                "Will retry after %.1f seconds.",
                self.url,
                e,
                self._retry_delay,
            )


def _resolve_spotlight_url(
    spotlight_config: "Any", sentry_logger: "Any"
) -> "Optional[str]":
    """
    Resolve the Spotlight URL based on config and environment variable.

    Implements precedence rules per the SDK spec:
    https://develop.sentry.dev/sdk/expected-features/spotlight/

    Returns the resolved URL string, or None if Spotlight should be disabled.
    """
    spotlight_env_value = os.environ.get("SENTRY_SPOTLIGHT")

    # Parse env var to determine if it's a boolean or URL
    spotlight_from_env: "Optional[bool]" = None
    spotlight_env_url: "Optional[str]" = None
    if spotlight_env_value:
        parsed = env_to_bool(spotlight_env_value, strict=True)
        if parsed is None:
            # It's a URL string
            spotlight_from_env = True
            spotlight_env_url = spotlight_env_value
        else:
            spotlight_from_env = parsed

    # Apply precedence rules per spec:
    # https://develop.sentry.dev/sdk/expected-features/spotlight/#precedence-rules
    if spotlight_config is False:
        # Config explicitly disables spotlight - warn if env var was set
        if spotlight_from_env:
            sentry_logger.warning(
                "Spotlight is disabled via spotlight=False config option, "
                "ignoring SENTRY_SPOTLIGHT environment variable."
            )
        return None
    elif spotlight_config is True:
        # Config enables spotlight with boolean true
        # If env var has URL, use env var URL per spec
        if spotlight_env_url:
            return spotlight_env_url
        else:
            return DEFAULT_SPOTLIGHT_URL
    elif isinstance(spotlight_config, str):
        # Config has URL string - use config URL, warn if env var differs
        if spotlight_env_value and spotlight_env_value != spotlight_config:
            sentry_logger.warning(
                "Spotlight URL from config (%s) takes precedence over "
                "SENTRY_SPOTLIGHT environment variable (%s).",
                spotlight_config,
                spotlight_env_value,
            )
        return spotlight_config
    elif spotlight_config is None:
        # No config - use env var
        if spotlight_env_url:
            return spotlight_env_url
        elif spotlight_from_env:
            return DEFAULT_SPOTLIGHT_URL
        # else: stays None (disabled)

    return None


def setup_spotlight(options: "Dict[str, Any]") -> "Optional[SpotlightClient]":
    url = _resolve_spotlight_url(options.get("spotlight"), sentry_logger)

    if url is None:
        return None

    # Only set up logging handler when spotlight is actually enabled
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter(" [spotlight] %(levelname)s: %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

    # Update options with resolved URL for consistency
    options["spotlight"] = url

    client = SpotlightClient(url)
    logger.info("Enabled Spotlight using sidecar at %s", url)

    return client
