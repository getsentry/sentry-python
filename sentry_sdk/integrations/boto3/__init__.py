from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.utils import parse_version

try:
    from botocore import __version__ as BOTOCORE_VERSION
except ImportError:
    raise DidNotEnable("botocore is not installed")

_SPAN_ORIGIN = "auto.http.boto3"


class Boto3Integration(Integration):
    origin = _SPAN_ORIGIN

    @staticmethod
    def setup_once() -> None:
        version = parse_version(BOTOCORE_VERSION)
        _check_minimum_version(Boto3Integration, version, "botocore")

        # local import to avoid import cycle
        from sentry_sdk.integrations.boto3._client import _patch_botocore_client

        _patch_botocore_client()
