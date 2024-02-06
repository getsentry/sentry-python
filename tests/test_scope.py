import copy
import os
import pytest

from sentry_sdk import scope
from sentry_sdk import capture_exception, new_scope, isolated_scope
from sentry_sdk.client import Client, NoopClient
from sentry_sdk.scope import Scope, ScopeType

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


@pytest.fixture
def clean_scopes():
    scope._global_scope = None
    scope._isolation_scope.set(None)
    scope._current_scope.set(None)


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


def test_scope_client():
    scope = Scope(ty="test_something")
    assert scope._type == "test_something"
    assert scope.client is not None
    assert scope.client.__class__ == NoopClient

    custom_client = Client()
    scope = Scope(ty="test_more", client=custom_client)
    assert scope._type == "test_more"
    assert scope.client is not None
    assert scope.client.__class__ == Client
    assert scope.client == custom_client


def test_get_current_scope():
    scope = Scope.get_current_scope()
    assert scope is not None
    assert scope.__class__ == Scope
    assert scope._type == ScopeType.CURRENT


def test_get_isolation_scope():
    scope = Scope.get_isolation_scope()
    assert scope is not None
    assert scope.__class__ == Scope
    assert scope._type == ScopeType.ISOLATION


def test_get_global_scope():
    scope = Scope.get_global_scope()
    assert scope is not None
    assert scope.__class__ == Scope
    assert scope._type == ScopeType.GLOBAL


def test_get_client(clean_scopes):
    client = Scope.get_client()
    assert client is not None
    assert client.__class__ == NoopClient
    assert not client.is_active()


def test_set_client():
    client1 = Client()
    client2 = Client()
    client3 = Client()

    current_scope = Scope.get_current_scope()
    isolation_scope = Scope.get_isolation_scope()
    global_scope = Scope.get_global_scope()

    current_scope.set_client(client1)
    isolation_scope.set_client(client2)
    global_scope.set_client(client3)

    client = Scope.get_client()
    assert client == client1

    current_scope.set_client(None)
    isolation_scope.set_client(client2)
    global_scope.set_client(client3)

    client = Scope.get_client()
    assert client == client2

    current_scope.set_client(None)
    isolation_scope.set_client(None)
    global_scope.set_client(client3)

    client = Scope.get_client()
    assert client == client3


def test_is_forked():
    scope = Scope()
    assert not scope.is_forked


def test_fork():
    scope = Scope()
    forked_scope = scope.fork()

    assert forked_scope.is_forked
    assert not scope.is_forked
    assert scope != forked_scope
    assert forked_scope.original_scope == scope


def test_isolate():
    isolation_scope_before = Scope.get_isolation_scope()

    scope = Scope()
    scope.isolate()

    isolation_scope_after = Scope.get_isolation_scope()

    assert isolation_scope_after != isolation_scope_before
    assert isolation_scope_after.is_forked
    assert isolation_scope_after.original_scope == isolation_scope_before
    assert not scope.is_forked


def test_get_global_scope_tags(clean_scopes):
    global_scope1 = Scope.get_global_scope()
    global_scope2 = Scope.get_global_scope()
    assert global_scope1 == global_scope2
    assert global_scope1.client.__class__ == NoopClient
    assert not global_scope1.client.is_active()
    assert global_scope2.client.__class__ == NoopClient
    assert not global_scope2.client.is_active()

    global_scope1.set_tag("tag1", "value")
    tags_scope1 = global_scope1._tags
    tags_scope2 = global_scope2._tags
    assert tags_scope1 == tags_scope2 == {"tag1": "value"}
    assert global_scope1.client.__class__ == NoopClient
    assert not global_scope1.client.is_active()
    assert global_scope2.client.__class__ == NoopClient
    assert not global_scope2.client.is_active()


def test_get_global_with_new_scope():
    original_global_scope = Scope.get_global_scope()

    with new_scope() as scope:
        in_with_global_scope = Scope.get_global_scope()

        assert scope is not in_with_global_scope
        assert in_with_global_scope is original_global_scope

    after_with_global_scope = Scope.get_global_scope()
    assert after_with_global_scope is original_global_scope


def test_get_global_with_isolated_scope():
    original_global_scope = Scope.get_global_scope()

    with isolated_scope() as scope:
        in_with_global_scope = Scope.get_global_scope()

        assert scope is not in_with_global_scope
        assert in_with_global_scope is original_global_scope

    after_with_global_scope = Scope.get_global_scope()
    assert after_with_global_scope is original_global_scope


def test_get_isolation_scope_tags(clean_scopes):
    isolation_scope1 = Scope.get_isolation_scope()
    isolation_scope2 = Scope.get_isolation_scope()
    assert isolation_scope1 == isolation_scope2
    assert isolation_scope1.client.__class__ == NoopClient
    assert not isolation_scope1.client.is_active()
    assert isolation_scope2.client.__class__ == NoopClient
    assert not isolation_scope2.client.is_active()

    isolation_scope1.set_tag("tag1", "value")
    tags_scope1 = isolation_scope1._tags
    tags_scope2 = isolation_scope2._tags
    assert tags_scope1 == tags_scope2 == {"tag1": "value"}
    assert isolation_scope1.client.__class__ == NoopClient
    assert not isolation_scope1.client.is_active()
    assert isolation_scope2.client.__class__ == NoopClient
    assert not isolation_scope2.client.is_active()


def test_with_isolated_scope():
    original_current_scope = Scope.get_current_scope()
    original_isolation_scope = Scope.get_isolation_scope()

    with isolated_scope() as scope:
        in_with_current_scope = Scope.get_current_scope()
        in_with_isolation_scope = Scope.get_isolation_scope()

        assert scope is in_with_isolation_scope
        assert in_with_current_scope is not original_current_scope
        assert in_with_isolation_scope is not original_isolation_scope

    after_with_current_scope = Scope.get_current_scope()
    after_with_isolation_scope = Scope.get_isolation_scope()
    assert after_with_current_scope is original_current_scope
    assert after_with_isolation_scope is original_isolation_scope


def test_get_current_scope_tags():
    scope1 = Scope.get_current_scope()
    scope2 = Scope.get_current_scope()
    assert id(scope1) == id(scope2)
    assert scope1.client.__class__ == NoopClient
    assert not scope1.client.is_active()
    assert scope2.client.__class__ == NoopClient
    assert not scope2.client.is_active()

    scope1.set_tag("tag1", "value")
    tags_scope1 = scope1._tags
    tags_scope2 = scope2._tags
    assert tags_scope1 == tags_scope2 == {"tag1": "value"}
    assert scope1.client.__class__ == NoopClient
    assert not scope1.client.is_active()
    assert scope2.client.__class__ == NoopClient
    assert not scope2.client.is_active()


def test_with_new_scope():
    original_current_scope = Scope.get_current_scope()
    original_isolation_scope = Scope.get_isolation_scope()

    with new_scope() as scope:
        in_with_current_scope = Scope.get_current_scope()
        in_with_isolation_scope = Scope.get_isolation_scope()

        assert scope is in_with_current_scope
        assert in_with_current_scope is not original_current_scope
        assert in_with_isolation_scope is original_isolation_scope

    after_with_current_scope = Scope.get_current_scope()
    after_with_isolation_scope = Scope.get_isolation_scope()
    assert after_with_current_scope is original_current_scope
    assert after_with_isolation_scope is original_isolation_scope


def test_fork_copy_on_write_set_tag():
    original_scope = Scope()
    original_scope.set_tag("scope", 0)

    forked_scope = original_scope.fork()
    assert id(original_scope._tags) == id(forked_scope._tags)

    forked_scope.set_tag("scope", 1)
    assert id(original_scope._tags) != id(forked_scope._tags)
    assert original_scope._tags == {"scope": 0}
    assert forked_scope._tags == {"scope": 1}


def test_fork_copy_on_write_remove_tag():
    original_scope = Scope()
    original_scope.set_tag("scope", 0)

    forked_scope = original_scope.fork()
    assert id(original_scope._tags) == id(forked_scope._tags)

    forked_scope.remove_tag("scope")
    assert id(original_scope._tags) != id(forked_scope._tags)
    assert original_scope._tags == {"scope": 0}
    assert forked_scope._tags == {}


# TODO: add tests for all properties of Scope that should have the copy-on-write feature
