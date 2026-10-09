import json
import time
from os import environ
from typing import TYPE_CHECKING

import urllib3

from sentry_sdk.integrations.aws_lambda.consts import (
    LAMBDA_METADATA_FAILURE_CACHE_SECONDS,
    LAMBDA_METADATA_PATH,
)

if TYPE_CHECKING:
    from typing import Optional, Tuple

_lambda_metadata_http = urllib3.PoolManager(
    timeout=urllib3.Timeout(connect=0.5, read=0.5),
    retries=False,
)
# (`expires_at`, `AvailabilityZoneID`), e.g. (1717987200.0, "use1-az1")
_lambda_metadata_cache: "Optional[Tuple[float, str]]" = None


def _get_availability_zone() -> "Optional[str]":
    now = time.time()
    global _lambda_metadata_cache
    if _lambda_metadata_cache is not None and _lambda_metadata_cache[0] > now:
        return _lambda_metadata_cache[1]

    # bearer token is required to prevent SSRF.
    response = _lambda_metadata_http.request(
        "GET",
        f"http://{environ['AWS_LAMBDA_METADATA_API']}{LAMBDA_METADATA_PATH}",
        headers={"Authorization": f"Bearer {environ['AWS_LAMBDA_METADATA_TOKEN']}"},
    )
    if response.status != 200:
        return None

    availability_zone = json.loads(response.data.decode("utf-8"))["AvailabilityZoneID"]

    # AWS shortens `max-age` during SnapStart initialization so restored
    # environments refresh `AvailabilityZoneID` for their new AZ.
    # https://docs.aws.amazon.com/lambda/latest/dg/configuration-metadata-endpoint.html
    max_age = response.headers["Cache-Control"].split("max-age=", 1)[1].split(",", 1)[0]
    _lambda_metadata_cache = (now + float(max_age), availability_zone)
    return availability_zone
