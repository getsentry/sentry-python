from datetime import datetime

from hypothesis import given, assume
import hypothesis.strategies as st

from sentry_sdk.serializer import Serializer


@given(dt=st.datetimes(timezones=st.just(None)))
def test_datetime_precision(dt, assert_semaphore_acceptance):
    assume(dt.year > 2000)
    serializer = Serializer()

    event = serializer.serialize_event({"timestamp": dt})
    normalized = assert_semaphore_acceptance(event)

    dt2 = datetime.utcfromtimestamp(normalized["timestamp"])

    # Float glitches can happen.
    assert abs(dt.microsecond - dt2.microsecond) < 1000
    assert dt.replace(microsecond=0) == dt2.replace(microsecond=0)
