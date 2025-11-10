from typing import Optional

import pytest

from sentry_sdk.integrations.django.middleware import _wrap_middleware


def _sync_capable_middleware_factory(sync_capable):
    # type: (Optional[bool]) -> type
    """Create a middleware class with a sync_capable attribute set to the value passed to the factory.
    If the factory is called with None, the middleware class will not have a sync_capable attribute.
    """
    sc = sync_capable  # rename so we can set sync_capable in the class

    class TestMiddleware:
        nonlocal sc
        if sc is not None:
            sync_capable = sc

    return TestMiddleware


@pytest.mark.parametrize(
    ("middleware", "sync_capable"),
    (
        (_sync_capable_middleware_factory(True), True),
        (_sync_capable_middleware_factory(False), False),
        (_sync_capable_middleware_factory(None), True),
    ),
)
@pytest.skip
def test_wrap_middleware_sync_capable_attribute(middleware, sync_capable):
    wrapped_middleware = _wrap_middleware(middleware, "test_middleware")

    assert wrapped_middleware.sync_capable is sync_capable
