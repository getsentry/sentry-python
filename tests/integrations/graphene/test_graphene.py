import pytest

import sentry_sdk
from sentry_sdk.integrations.graphene import GrapheneIntegration


def test_something(sentry_init, capture_events):
    assert False
