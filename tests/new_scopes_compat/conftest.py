import pytest
import sentry_sdk


@pytest.fixture(autouse=True)
def isolate_hub(suppress_deprecation_warnings):
    with sentry_sdk.Hub(None):
        yield
