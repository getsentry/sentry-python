import re

import pytest

from sentry_sdk.consts import DEFAULT_MAX_VALUE_LENGTH
from sentry_sdk.serializer import MAX_DATABAG_BREADTH, MAX_DATABAG_DEPTH, serialize

try:
    from hypothesis import given
    import hypothesis.strategies as st
except ImportError:
    pass
else:

    def test_bytes_serialization_decode_many(message_normalizer):
        @given(binary=st.binary(min_size=1))
        def inner(binary):
            result = message_normalizer(binary, should_repr_strings=False)
            assert result == binary.decode("utf-8", "replace")

        inner()

    def test_bytes_serialization_repr_many(message_normalizer):
        @given(binary=st.binary(min_size=1))
        def inner(binary):
            result = message_normalizer(binary, should_repr_strings=True)
            assert result == repr(binary)

        inner()


@pytest.fixture
def message_normalizer(validate_event_schema):
    def inner(message, **kwargs):
        event = serialize({"logentry": {"message": message}}, **kwargs)
        validate_event_schema(event)
        return event["logentry"]["message"]

    return inner


@pytest.fixture
def extra_normalizer(validate_event_schema):
    def inner(extra, **kwargs):
        event = serialize({"extra": {"foo": extra}}, **kwargs)
        validate_event_schema(event)
        return event["extra"]["foo"]

    return inner


@pytest.fixture
def body_normalizer(validate_event_schema):
    def inner(body, **kwargs):
        event = serialize({"request": {"data": body}}, **kwargs)
        validate_event_schema(event)
        return event["request"]["data"]

    return inner


def test_bytes_serialization_decode(message_normalizer):
    binary = b"abc123\x80\xf0\x9f\x8d\x95"
    result = message_normalizer(binary, should_repr_strings=False)
    assert result == "abc123\ufffd\U0001f355"


def test_bytes_serialization_repr(message_normalizer):
    binary = b"abc123\x80\xf0\x9f\x8d\x95"
    result = message_normalizer(binary, should_repr_strings=True)
    assert result == r"b'abc123\x80\xf0\x9f\x8d\x95'"


def test_bytearray_serialization_decode(message_normalizer):
    binary = bytearray(b"abc123\x80\xf0\x9f\x8d\x95")
    result = message_normalizer(binary, should_repr_strings=False)
    assert result == "abc123\ufffd\U0001f355"


def test_bytearray_serialization_repr(message_normalizer):
    binary = bytearray(b"abc123\x80\xf0\x9f\x8d\x95")
    result = message_normalizer(binary, should_repr_strings=True)
    assert result == r"bytearray(b'abc123\x80\xf0\x9f\x8d\x95')"


def test_memoryview_serialization_repr(message_normalizer):
    binary = memoryview(b"abc123\x80\xf0\x9f\x8d\x95")
    result = message_normalizer(binary, should_repr_strings=False)
    assert re.match(r"^<memory at 0x\w+>$", result)


def test_serialize_sets(extra_normalizer):
    result = extra_normalizer({1, 2, 3})
    assert result == [1, 2, 3]


def test_serialize_custom_mapping(extra_normalizer):
    class CustomReprDict(dict):
        def __sentry_repr__(self):
            return "custom!"

    result = extra_normalizer(CustomReprDict(one=1, two=2))
    assert result == "custom!"


def test_custom_mapping_doesnt_mess_with_mock(extra_normalizer):
    """
    Adding the __sentry_repr__ magic method check in the serializer
    shouldn't mess with how mock works. This broke some stuff when we added
    sentry_repr without the dunders.
    """
    mock = pytest.importorskip("unittest.mock")
    m = mock.Mock()
    extra_normalizer(m)
    assert len(m.mock_calls) == 0


def test_custom_repr(extra_normalizer):
    class Foo:
        pass

    def custom_repr(value):
        if isinstance(value, Foo):
            return "custom"
        else:
            return value

    result = extra_normalizer({"foo": Foo(), "string": "abc"}, custom_repr=custom_repr)
    assert result == {"foo": "custom", "string": "abc"}


def test_custom_repr_graceful_fallback_to_safe_repr(extra_normalizer):
    class Foo:
        pass

    def custom_repr(value):
        raise ValueError("oops")

    result = extra_normalizer({"foo": Foo()}, custom_repr=custom_repr)
    assert "Foo object" in result["foo"]


def test_trim_databag_breadth(body_normalizer):
    data = {
        "key{}".format(i): "value{}".format(i) for i in range(MAX_DATABAG_BREADTH + 10)
    }

    result = body_normalizer(data)

    assert len(result) == MAX_DATABAG_BREADTH
    for key, value in result.items():
        assert data.get(key) == value


def test_no_trimming_if_max_request_body_size_is_always(body_normalizer):
    data = {
        "key{}".format(i): "value{}".format(i) for i in range(MAX_DATABAG_BREADTH + 10)
    }
    curr = data
    for _ in range(MAX_DATABAG_DEPTH + 5):
        curr["nested"] = {}
        curr = curr["nested"]

    result = body_normalizer(data, max_request_body_size="always")

    assert result == data


def test_max_value_length_default(body_normalizer):
    data = {"key": "a" * (DEFAULT_MAX_VALUE_LENGTH * 10)}

    result = body_normalizer(data)

    assert len(result["key"]) == DEFAULT_MAX_VALUE_LENGTH  # fallback max length


def test_max_value_length(body_normalizer):
    data = {"key": "a" * 2000}

    max_value_length = 1800
    result = body_normalizer(data, max_value_length=max_value_length)

    assert len(result["key"]) == max_value_length
