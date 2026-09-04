import re
from array import array
from dataclasses import dataclass

import pytest

from sentry_sdk.serializer import (
    MAX_DATABAG_BREADTH,
    MAX_DATABAG_DEPTH,
    MAX_REPR_LENGTH,
    serialize,
)

try:
    import hypothesis.strategies as st
    from hypothesis import given
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


def test_no_value_truncation_by_default(body_normalizer):
    data = {"key": "a" * (10240)}

    result = body_normalizer(data)

    assert len(result["key"]) == 10240  # fallback max length


def test_max_value_length(body_normalizer):
    data = {"key": "a" * 2000}

    max_value_length = 1800
    result = body_normalizer(data, max_value_length=max_value_length)

    assert len(result["key"]) == max_value_length


def test_serialize_local_vars():
    # This was added to make sure we don't try to iterate over instances of
    # custom classes with an __iter__ method due to potential side effects
    class Custom:
        def __init__(self, items):
            self.items = items

        def __len__(self):
            return self.items.__len__()

        def __getitem__(self, item):
            return self.items.__getitem__(item)

        def __iter__(self):
            raise ValueError

    local_vars = {
        "str": "123",
        "bytes": b"123",
        "list": [1, 2, 3],
        "set": {1, 2, 3},
        "frozenset": frozenset([1, 2, 3]),
        "array": array("l", [1, 2, 3]),
        "custom": Custom([1, 2, 3]),
    }

    result = serialize(local_vars, is_vars=True)
    assert result["str"] == "'123'"
    assert result["bytes"] == "b'123'"
    assert result["list"] == ["1", "2", "3"]
    assert sorted(result["set"]) == ["1", "2", "3"]
    assert sorted(result["frozenset"]) == ["1", "2", "3"]
    assert result["array"] == ["1", "2", "3"]
    assert result["custom"].startswith(
        "<tests.test_serializer.test_serialize_local_vars.<locals>.Custom object at"
    )


def test_small_object_repr_is_unchanged():
    # A normal object/dataclass local is still serialized to its exact repr().
    @dataclass
    class Point:
        x: int
        y: str

    point = Point(1, "hi")
    result = serialize({"point": point}, is_vars=True)["point"]
    assert result == repr(point)
    assert "Point(x=1, y='hi')" in result


def test_large_object_repr_is_not_fully_materialized():
    # Regression test for #6649: serializing a local whose repr walks a large
    # object graph must not build the whole repr and then throw most of it
    # away. FastAPI's _IncludedRouter is the real-world trigger; here we use a
    # dataclass with an oversized field followed by a sentinel whose __repr__
    # records whether it was reached.
    reached = []

    class Tail:
        def __repr__(self):
            reached.append(True)
            return "<tail>"

    @dataclass
    class Big:
        head: list
        tail: object

    big = Big(head=list(range(200000)), tail=Tail())

    result = serialize({"big": big}, is_vars=True)["big"]

    # We stopped before reaching tail instead of rendering the full graph, so
    # tail was never repr'd. Reverting the fix walks the whole graph and trips
    # this.
    assert reached == []
    # What we kept is a real, truncation-marked prefix of the object's repr.
    assert "Big(head=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10," in result
    assert result.endswith("...")
    assert MAX_REPR_LENGTH <= len(result) < MAX_REPR_LENGTH + 100


def test_large_object_repr_respects_max_value_length():
    # With max_value_length set, the bounded repr yields exactly what the old
    # build-the-whole-repr-then-truncate path produced: capped to the limit
    # and a genuine prefix of repr().
    @dataclass
    class Big:
        data: list

    big = Big(data=list(range(100000)))

    result = serialize({"big": big}, is_vars=True, max_value_length=1024)["big"]

    assert len(result) == 1024
    assert result.endswith("...")
    assert repr(big).startswith(result[:-3])
