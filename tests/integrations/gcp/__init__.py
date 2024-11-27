import pytest
import os


if "gcp" not in os.environ.get("TOX_ENV_NAME", ""):
    pytest.skip("GCP tests only run in GCP environment", allow_module_level=True)
