from datetime import datetime

from hypothesis import given, assume, example
import hypothesis.strategies as st

from sentry_sdk.serializer import Serializer


@given(dt=st.datetimes(timezones=st.just(None)))
@example(dt=datetime(2001, 1, 1, 0, 0, 0, 999500))
def test_datetime_precision(dt, assert_semaphore_acceptance):
    assume(dt.year > 2000)
    serializer = Serializer()

    event = serializer.serialize_event({"timestamp": dt})
    normalized = assert_semaphore_acceptance(event)

    dt2 = datetime.utcfromtimestamp(normalized["timestamp"])

    # Float glitches can happen, and more glitches can happen
    # because we try to work around some float glitches in semaphore
    assert (dt - dt2).total_seconds() < 1.0
