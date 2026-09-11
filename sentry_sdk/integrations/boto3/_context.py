from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Dict, Optional

    from botocore.client import BaseClient


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

    def __init__(
        self,
        client: "BaseClient",
        operation_name: str,
        params: "Any",
    ) -> None:
        client_meta = client.meta
        service_model = client_meta.service_model
        service_id = service_model.service_id

        # botocore's internal identifier, e.g. `apigateway`.
        self.service_name: str = service_model.service_name
        # modeled AWS service identity used in span names, e.g. `API Gateway`.
        self.service_id: str = str(service_id)
        self.service_id_hyphenized: str = service_id.hyphenize()
        self.operation_name: str = operation_name
        self.region_name: "Optional[str]" = getattr(client_meta, "region_name", None)
        self.endpoint_url: "Optional[str]" = getattr(client_meta, "endpoint_url", None)
        self.params: "Dict[str, Any]" = dict(params) if isinstance(params, dict) else {}
