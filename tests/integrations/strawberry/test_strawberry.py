import pytest

import sentry_sdk
from sentry_sdk.integrations.strawberry import StrawberryIntegration


def test_something(sentry_init, capture_events):
    assert False
