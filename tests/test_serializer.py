from datetime import datetime


import pytest

from sentry_sdk.serializer import serialize

try:
    from hypothesis import given, example
    import hypothesis.strategies as st
except ImportError:
    pass
else:

    @given(
        dt=st.datetimes(
            min_value=datetime(2000, 1, 1, 0, 0, 0), timezones=st.just(None)
        )
    )
    @example(dt=datetime(2001, 1, 1, 0, 0, 0, 999500))
    def test_datetime_precision(dt, semaphore_normalize):
        event = serialize({"timestamp": dt})
        normalized = semaphore_normalize(event)

        if normalized is None:
            pytest.skip("no semaphore available")

        dt2 = datetime.utcfromtimestamp(normalized["timestamp"])

        # Float glitches can happen, and more glitches can happen
        # because we try to work around some float glitches in semaphore
        assert (dt - dt2).total_seconds() < 1.0
