import pytest

import sentry_sdk
from sentry_sdk.integrations.graphql.ariadne import AriadneIntegration


def test_something(sentry_init, capture_events):
    assert False
