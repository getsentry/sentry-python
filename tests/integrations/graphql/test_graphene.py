import pytest

import sentry_sdk
from sentry_sdk.integrations.graphql.graphene import GrapheneIntegration


def test_something(sentry_init, capture_events):
    assert False
