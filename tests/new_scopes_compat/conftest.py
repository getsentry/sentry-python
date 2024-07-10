import pytest
import sentry_sdk


@pytest.fixture(autouse=True)
def isolate_hub():
    with sentry_sdk.Hub(None):
        yield
