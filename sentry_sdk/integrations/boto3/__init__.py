from sentry_sdk.integrations import Integration


class Boto3Integration(Integration):
    identifier = "boto3"
    origin = f"auto.http.{identifier}"

    @staticmethod
    def setup_once() -> None:
        # local import to avoid import cycle
        from sentry_sdk.integrations.boto3._client import _patch_botocore_client

        _patch_botocore_client()
