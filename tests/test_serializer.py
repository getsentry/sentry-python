from datetime import datetime
import sys

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
    def test_datetime_precision(dt, validate_event_schema):
        event = serialize({"timestamp": dt})
        validate_event_schema(event)

        dt2 = datetime.utcfromtimestamp(event["timestamp"])

        # Float glitches can happen, and more glitches can happen
        # because we try to work around some float glitches in relay
        assert (dt - dt2).total_seconds() < 1.0

    @given(binary=st.binary(min_size=1))
    def test_bytes_serialization_decode_many(binary, message_normalizer):
        result = message_normalizer(binary, should_repr_strings=False)
        assert result == binary.decode("utf-8", "replace")

    @given(binary=st.binary(min_size=1))
    def test_bytes_serialization_repr_many(binary, message_normalizer):
        result = message_normalizer(binary, should_repr_strings=True)
        assert result == repr(binary)


@pytest.fixture
def message_normalizer(validate_event_schema):
    def inner(message, **kwargs):
        event = serialize({"logentry": {"message": message}}, **kwargs)
        validate_event_schema(event)
        return event["logentry"]["message"]

    return inner


@pytest.fixture
def extra_normalizer(validate_event_schema):
    def inner(message, **kwargs):
        event = serialize({"extra": {"foo": message}}, **kwargs)
        validate_event_schema(event)
        return event["extra"]["foo"]

    return inner


def test_bytes_serialization_decode(message_normalizer):
    binary = b"abc123\x80\xf0\x9f\x8d\x95"
    result = message_normalizer(binary, should_repr_strings=False)
    assert result == u"abc123\ufffd\U0001f355"


@pytest.mark.xfail(sys.version_info < (3,), reason="Known safe_repr bugs in Py2.7")
def test_bytes_serialization_repr(message_normalizer):
    binary = b"abc123\x80\xf0\x9f\x8d\x95"
    result = message_normalizer(binary, should_repr_strings=True)
    assert result == r"b'abc123\x80\xf0\x9f\x8d\x95'"


def test_serialize_sets(extra_normalizer):
    result = extra_normalizer({1, 2, 3})
    assert result == [1, 2, 3]
