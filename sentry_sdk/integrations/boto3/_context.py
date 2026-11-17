from typing import TYPE_CHECKING

from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any, Dict, Optional

try:
    from botocore.client import BaseClient
except ImportError:
    raise DidNotEnable("botocore not installed")


class AwsCallContext:
    __slots__ = (
        "service_name",
        "service_id",
        "service_id_hyphenized",
        "operation_name",
        "region_name",
        "endpoint_url",
        "params",
    )

    def __init__(self, operation_name: str, params: "Dict[str, Any]") -> None:
        self.operation_name: "str" = operation_name
        self.params: "Dict[str, Any]" = dict(params)
        self.service_name: "Optional[str]" = None
        self.service_id: "Optional[str]" = None
        self.service_id_hyphenized: "Optional[str]" = None
        self.region_name: "Optional[str]" = None
        self.endpoint_url: "Optional[str]" = None

    def add_metadata(self, client: "BaseClient") -> None:
        with capture_internal_exceptions():
            service_model = client.meta.service_model
            # botocore's internal identifier, e.g. `apigateway`.
            self.service_name = service_model.service_name
            service_id = service_model.service_id
            # modeled AWS service identity used in span names, e.g. `API Gateway`.
            self.service_id = str(service_id)
            self.service_id_hyphenized = service_id.hyphenize()

        with capture_internal_exceptions():
            self.region_name = client.meta.region_name

        with capture_internal_exceptions():
            self.endpoint_url = client.meta.endpoint_url
