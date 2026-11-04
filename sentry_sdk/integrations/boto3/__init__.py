from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.integrations.boto3._client import _patch_botocore_client
from sentry_sdk.integrations.boto3._instrumentation import _patch_event_stream
from sentry_sdk.integrations.boto3.consts import IDENTIFIER, ORIGIN
from sentry_sdk.utils import parse_version

try:
    from botocore import __version__ as BOTOCORE_VERSION
except ImportError:
    raise DidNotEnable("botocore is not installed")


class Boto3Integration(Integration):
    identifier = IDENTIFIER
    origin = ORIGIN

    def __init__(self: "Boto3Integration", include_prompts: bool = True) -> None:
        # whether to record gen_ai prompts and responses, e.g. of Bedrock Runtime
        # calls, when `send_default_pii` is on and `data_collection` isn't set.
        self.include_prompts = include_prompts

    @staticmethod
    def setup_once() -> None:
        version = parse_version(BOTOCORE_VERSION)
        _check_minimum_version(Boto3Integration, version, "botocore")

        _patch_botocore_client()
        _patch_event_stream()
