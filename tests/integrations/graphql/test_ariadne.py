import pytest

import sentry_sdk
from sentry_sdk.integrations.graphql.ariadne import AriadneIntegration


def test_has_context(sentry_init, capture_events):
    assert False
