import sys

import pytest
from sentry_sdk.types import Event, Hint


@pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="Type hinting with `|` is available in Python 3.10+",
)
def test_event_or_none_runtime():
    """
    Ensures that the `Event` type's runtime value supports the `|` operation with `None`.
    This test is needed to ensure that using an `Event | None` type hint (e.g. for
    `before_send`'s return value) does not raise a TypeError at runtime.
    """
    Event | None


@pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="Type hinting with `|` is available in Python 3.10+",
)
def test_hint_or_none_runtime():
    """
    Analogue to `test_event_or_none_runtime`, but for the `Hint` type.
    """
    Hint | None
