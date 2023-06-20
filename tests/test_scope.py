import copy
import os
import pytest
from sentry_sdk import capture_exception
from sentry_sdk.scope import Scope

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


def test_copying():
    s1 = Scope()
    s1.fingerprint = {}
    s1.set_tag("foo", "bar")

    s2 = copy.copy(s1)
    assert "foo" in s2._tags

    s1.set_tag("bam", "baz")
    assert "bam" in s1._tags
    assert "bam" not in s2._tags

    assert s1._fingerprint is s2._fingerprint


def test_merging(sentry_init, capture_events):
    sentry_init()

    s = Scope()
    s.set_user({"id": "42"})

    events = capture_events()

    capture_exception(NameError(), scope=s)

    (event,) = events
    assert event["user"] == {"id": "42"}


def test_common_args():
    s = Scope()
    s.update_from_kwargs(
        user={"id": 23},
        level="warning",
        extras={"k": "v"},
        contexts={"os": {"name": "Blafasel"}},
        tags={"x": "y"},
        fingerprint=["foo"],
    )

    s2 = Scope()
    s2.set_extra("foo", "bar")
    s2.set_tag("a", "b")
    s2.set_context("device", {"a": "b"})
    s2.update_from_scope(s)

    assert s._user == {"id": 23}
    assert s._level == "warning"
    assert s._extras == {"k": "v"}
    assert s._contexts == {"os": {"name": "Blafasel"}}
    assert s._tags == {"x": "y"}
    assert s._fingerprint == ["foo"]

    assert s._user == s2._user
    assert s._level == s2._level
    assert s._fingerprint == s2._fingerprint
    assert s2._extras == {"k": "v", "foo": "bar"}
    assert s2._tags == {"a": "b", "x": "y"}
    assert s2._contexts == {"os": {"name": "Blafasel"}, "device": {"a": "b"}}


BAGGAGE_VALUE = (
    "other-vendor-value-1=foo;bar;baz, sentry-trace_id=771a43a4192642f0b136d5159a501700, "
    "sentry-public_key=49d0f7386ad645858ae85020e393bef3, sentry-sample_rate=0.01337, "
    "sentry-user_id=Am%C3%A9lie, other-vendor-value-2=foo;bar;"
)

SENTRY_TRACE_VALUE = "771a43a4192642f0b136d5159a501700-1234567890abcdef-1"


@pytest.mark.parametrize(
    "env,excepted_value",
    [
        (
            {
                "SENTRY_TRACE": SENTRY_TRACE_VALUE,
            },
            {
                "sentry-trace": SENTRY_TRACE_VALUE,
            },
        ),
        (
            {
                "SENTRY_BAGGAGE": BAGGAGE_VALUE,
            },
            {
                "baggage": BAGGAGE_VALUE,
            },
        ),
        (
            {
                "SENTRY_TRACE": SENTRY_TRACE_VALUE,
                "SENTRY_BAGGAGE": BAGGAGE_VALUE,
            },
            {
                "sentry-trace": SENTRY_TRACE_VALUE,
                "baggage": BAGGAGE_VALUE,
            },
        ),
        (
            {
                "SENTRY_USE_ENVIRONMENT": "",
                "SENTRY_TRACE": SENTRY_TRACE_VALUE,
                "SENTRY_BAGGAGE": BAGGAGE_VALUE,
            },
            {
                "sentry-trace": SENTRY_TRACE_VALUE,
                "baggage": BAGGAGE_VALUE,
            },
        ),
        (
            {
                "SENTRY_USE_ENVIRONMENT": "True",
                "SENTRY_TRACE": SENTRY_TRACE_VALUE,
                "SENTRY_BAGGAGE": BAGGAGE_VALUE,
            },
            {
                "sentry-trace": SENTRY_TRACE_VALUE,
                "baggage": BAGGAGE_VALUE,
            },
        ),
        (
            {
                "SENTRY_USE_ENVIRONMENT": "no",
                "SENTRY_TRACE": SENTRY_TRACE_VALUE,
                "SENTRY_BAGGAGE": BAGGAGE_VALUE,
            },
            None,
        ),
        (
            {
                "SENTRY_USE_ENVIRONMENT": "True",
                "MY_OTHER_VALUE": "asdf",
                "SENTRY_RELEASE": "1.0.0",
            },
            None,
        ),
    ],
)
def test_load_trace_data_from_env(env, excepted_value):
    new_env = os.environ.copy()
    new_env.update(env)

    with mock.patch.dict(os.environ, new_env):
        s = Scope()
        incoming_trace_data = s._load_trace_data_from_env()
        assert incoming_trace_data == excepted_value
