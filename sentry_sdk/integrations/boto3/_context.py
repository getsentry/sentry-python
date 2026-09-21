from typing import TYPE_CHECKING

from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any, Optional

try:
    from botocore.client import BaseClient
except ImportError:
    raise DidNotEnable("botocore not installed")


class AwsCallContext:
    __slots__ = (
        "service_id",
        "service_id_hyphenized",
        "operation_name",
    )

    def __init__(self, operation_name: str) -> None:
        self.operation_name: str = operation_name
        self.service_id: "Optional[str]" = None
        self.service_id_hyphenized: "Optional[str]" = None

    def add_metadata(self, client: "BaseClient") -> None:
        def _get_attr(obj: "Any", name: str) -> "Any":
            if obj is None:
                return None

            with capture_internal_exceptions():
                return getattr(obj, name)

        client_meta = _get_attr(client, "meta")
        service_model = _get_attr(client_meta, "service_model")

        # modeled AWS service identity used in span names, e.g. `API Gateway`.
        service_id = _get_attr(service_model, "service_id")
        if service_id is not None:
            with capture_internal_exceptions():
                self.service_id = str(service_id)
            with capture_internal_exceptions():
                self.service_id_hyphenized = service_id.hyphenize()
