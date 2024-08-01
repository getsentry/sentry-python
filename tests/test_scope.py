import copy
import os
import pytest
from unittest import mock

import sentry_sdk
from sentry_sdk import (
    capture_exception,
    isolation_scope,
    new_scope,
)
from sentry_sdk.client import Client, NonRecordingClient
from sentry_sdk.scope import (
    Scope,
    ScopeType,
    use_isolation_scope,
    use_scope,
    should_send_default_pii,
)


SLOTS_NOT_COPIED = {"client"}
"""__slots__ that are not copied when copying a Scope object."""


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


def test_all_slots_copied():
    scope = Scope()
    scope_copy = copy.copy(scope)

    # Check all attributes are copied
    for attr in set(Scope.__slots__) - SLOTS_NOT_COPIED:
        assert getattr(scope_copy, attr) == getattr(scope, attr)


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
    assert scope.client.__class__ == NonRecordingClient

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


def test_get_client():
    client = Scope.get_client()
    assert client is not None
    assert client.__class__ == NonRecordingClient
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


def test_fork():
    scope = Scope()
    forked_scope = scope.fork()

    assert scope != forked_scope


def test_get_global_scope_tags():
    global_scope1 = Scope.get_global_scope()
    global_scope2 = Scope.get_global_scope()
    assert global_scope1 == global_scope2
    assert global_scope1.client.__class__ == NonRecordingClient
    assert not global_scope1.client.is_active()
    assert global_scope2.client.__class__ == NonRecordingClient
    assert not global_scope2.client.is_active()

    global_scope1.set_tag("tag1", "value")
    tags_scope1 = global_scope1._tags
    tags_scope2 = global_scope2._tags
    assert tags_scope1 == tags_scope2 == {"tag1": "value"}
    assert global_scope1.client.__class__ == NonRecordingClient
    assert not global_scope1.client.is_active()
    assert global_scope2.client.__class__ == NonRecordingClient
    assert not global_scope2.client.is_active()


def test_get_global_with_scope():
    original_global_scope = Scope.get_global_scope()

    with new_scope() as scope:
        in_with_global_scope = Scope.get_global_scope()

        assert scope is not in_with_global_scope
        assert in_with_global_scope is original_global_scope

    after_with_global_scope = Scope.get_global_scope()
    assert after_with_global_scope is original_global_scope


def test_get_global_with_isolation_scope():
    original_global_scope = Scope.get_global_scope()

    with isolation_scope() as scope:
        in_with_global_scope = Scope.get_global_scope()

        assert scope is not in_with_global_scope
        assert in_with_global_scope is original_global_scope

    after_with_global_scope = Scope.get_global_scope()
    assert after_with_global_scope is original_global_scope


def test_get_isolation_scope_tags():
    isolation_scope1 = Scope.get_isolation_scope()
    isolation_scope2 = Scope.get_isolation_scope()
    assert isolation_scope1 == isolation_scope2
    assert isolation_scope1.client.__class__ == NonRecordingClient
    assert not isolation_scope1.client.is_active()
    assert isolation_scope2.client.__class__ == NonRecordingClient
    assert not isolation_scope2.client.is_active()

    isolation_scope1.set_tag("tag1", "value")
    tags_scope1 = isolation_scope1._tags
    tags_scope2 = isolation_scope2._tags
    assert tags_scope1 == tags_scope2 == {"tag1": "value"}
    assert isolation_scope1.client.__class__ == NonRecordingClient
    assert not isolation_scope1.client.is_active()
    assert isolation_scope2.client.__class__ == NonRecordingClient
    assert not isolation_scope2.client.is_active()


def test_get_current_scope_tags():
    scope1 = Scope.get_current_scope()
    scope2 = Scope.get_current_scope()
    assert id(scope1) == id(scope2)
    assert scope1.client.__class__ == NonRecordingClient
    assert not scope1.client.is_active()
    assert scope2.client.__class__ == NonRecordingClient
    assert not scope2.client.is_active()

    scope1.set_tag("tag1", "value")
    tags_scope1 = scope1._tags
    tags_scope2 = scope2._tags
    assert tags_scope1 == tags_scope2 == {"tag1": "value"}
    assert scope1.client.__class__ == NonRecordingClient
    assert not scope1.client.is_active()
    assert scope2.client.__class__ == NonRecordingClient
    assert not scope2.client.is_active()


def test_with_isolation_scope():
    original_current_scope = Scope.get_current_scope()
    original_isolation_scope = Scope.get_isolation_scope()

    with isolation_scope() as scope:
        assert scope._type == ScopeType.ISOLATION

        in_with_current_scope = Scope.get_current_scope()
        in_with_isolation_scope = Scope.get_isolation_scope()

        assert scope is in_with_isolation_scope
        assert in_with_current_scope is not original_current_scope
        assert in_with_isolation_scope is not original_isolation_scope

    after_with_current_scope = Scope.get_current_scope()
    after_with_isolation_scope = Scope.get_isolation_scope()
    assert after_with_current_scope is original_current_scope
    assert after_with_isolation_scope is original_isolation_scope


def test_with_isolation_scope_data():
    """
    When doing `with isolation_scope()` the isolation *and* the current scope are forked,
    to prevent that by setting tags on the current scope in the context manager, data
    bleeds to the outer current scope.
    """
    isolation_scope_before = Scope.get_isolation_scope()
    current_scope_before = Scope.get_current_scope()

    isolation_scope_before.set_tag("before_isolation_scope", 1)
    current_scope_before.set_tag("before_current_scope", 1)

    with isolation_scope() as scope:
        assert scope._type == ScopeType.ISOLATION

        isolation_scope_in = Scope.get_isolation_scope()
        current_scope_in = Scope.get_current_scope()

        assert isolation_scope_in._tags == {"before_isolation_scope": 1}
        assert current_scope_in._tags == {"before_current_scope": 1}
        assert scope._tags == {"before_isolation_scope": 1}

        scope.set_tag("in_with_scope", 1)

        assert isolation_scope_in._tags == {
            "before_isolation_scope": 1,
            "in_with_scope": 1,
        }
        assert current_scope_in._tags == {"before_current_scope": 1}
        assert scope._tags == {"before_isolation_scope": 1, "in_with_scope": 1}

        isolation_scope_in.set_tag("in_with_isolation_scope", 1)

        assert isolation_scope_in._tags == {
            "before_isolation_scope": 1,
            "in_with_scope": 1,
            "in_with_isolation_scope": 1,
        }
        assert current_scope_in._tags == {"before_current_scope": 1}
        assert scope._tags == {
            "before_isolation_scope": 1,
            "in_with_scope": 1,
            "in_with_isolation_scope": 1,
        }

        current_scope_in.set_tag("in_with_current_scope", 1)

        assert isolation_scope_in._tags == {
            "before_isolation_scope": 1,
            "in_with_scope": 1,
            "in_with_isolation_scope": 1,
        }
        assert current_scope_in._tags == {
            "before_current_scope": 1,
            "in_with_current_scope": 1,
        }
        assert scope._tags == {
            "before_isolation_scope": 1,
            "in_with_scope": 1,
            "in_with_isolation_scope": 1,
        }

    isolation_scope_after = Scope.get_isolation_scope()
    current_scope_after = Scope.get_current_scope()

    isolation_scope_after.set_tag("after_isolation_scope", 1)

    assert isolation_scope_after._tags == {
        "before_isolation_scope": 1,
        "after_isolation_scope": 1,
    }
    assert current_scope_after._tags == {"before_current_scope": 1}

    current_scope_after.set_tag("after_current_scope", 1)

    assert isolation_scope_after._tags == {
        "before_isolation_scope": 1,
        "after_isolation_scope": 1,
    }
    assert current_scope_after._tags == {
        "before_current_scope": 1,
        "after_current_scope": 1,
    }


def test_with_use_isolation_scope():
    original_isolation_scope = Scope.get_isolation_scope()
    original_current_scope = Scope.get_current_scope()
    custom_isolation_scope = Scope()

    with use_isolation_scope(custom_isolation_scope) as scope:
        assert scope._type is None  # our custom scope has not type set

        in_with_isolation_scope = Scope.get_isolation_scope()
        in_with_current_scope = Scope.get_current_scope()

        assert scope is custom_isolation_scope
        assert scope is in_with_isolation_scope
        assert scope is not in_with_current_scope
        assert scope is not original_isolation_scope
        assert scope is not original_current_scope
        assert in_with_isolation_scope is not original_isolation_scope
        assert in_with_current_scope is not original_current_scope

    after_with_current_scope = Scope.get_current_scope()
    after_with_isolation_scope = Scope.get_isolation_scope()

    assert after_with_isolation_scope is original_isolation_scope
    assert after_with_current_scope is original_current_scope
    assert after_with_isolation_scope is not custom_isolation_scope
    assert after_with_current_scope is not custom_isolation_scope


def test_with_use_isolation_scope_data():
    isolation_scope_before = Scope.get_isolation_scope()
    current_scope_before = Scope.get_current_scope()
    custom_isolation_scope = Scope()

    isolation_scope_before.set_tag("before_isolation_scope", 1)
    current_scope_before.set_tag("before_current_scope", 1)
    custom_isolation_scope.set_tag("before_custom_isolation_scope", 1)

    with use_isolation_scope(custom_isolation_scope) as scope:
        assert scope._type is None  # our custom scope has not type set

        isolation_scope_in = Scope.get_isolation_scope()
        current_scope_in = Scope.get_current_scope()

        assert isolation_scope_in._tags == {"before_custom_isolation_scope": 1}
        assert current_scope_in._tags == {"before_current_scope": 1}
        assert scope._tags == {"before_custom_isolation_scope": 1}

        scope.set_tag("in_with_scope", 1)

        assert isolation_scope_in._tags == {
            "before_custom_isolation_scope": 1,
            "in_with_scope": 1,
        }
        assert current_scope_in._tags == {"before_current_scope": 1}
        assert scope._tags == {"before_custom_isolation_scope": 1, "in_with_scope": 1}

        isolation_scope_in.set_tag("in_with_isolation_scope", 1)

        assert isolation_scope_in._tags == {
            "before_custom_isolation_scope": 1,
            "in_with_scope": 1,
            "in_with_isolation_scope": 1,
        }
        assert current_scope_in._tags == {"before_current_scope": 1}
        assert scope._tags == {
            "before_custom_isolation_scope": 1,
            "in_with_scope": 1,
            "in_with_isolation_scope": 1,
        }

        current_scope_in.set_tag("in_with_current_scope", 1)

        assert isolation_scope_in._tags == {
            "before_custom_isolation_scope": 1,
            "in_with_scope": 1,
            "in_with_isolation_scope": 1,
        }
        assert current_scope_in._tags == {
            "before_current_scope": 1,
            "in_with_current_scope": 1,
        }
        assert scope._tags == {
            "before_custom_isolation_scope": 1,
            "in_with_scope": 1,
            "in_with_isolation_scope": 1,
        }

    assert custom_isolation_scope._tags == {
        "before_custom_isolation_scope": 1,
        "in_with_scope": 1,
        "in_with_isolation_scope": 1,
    }
    isolation_scope_after = Scope.get_isolation_scope()
    current_scope_after = Scope.get_current_scope()

    isolation_scope_after.set_tag("after_isolation_scope", 1)

    assert isolation_scope_after._tags == {
        "before_isolation_scope": 1,
        "after_isolation_scope": 1,
    }
    assert current_scope_after._tags == {"before_current_scope": 1}
    assert custom_isolation_scope._tags == {
        "before_custom_isolation_scope": 1,
        "in_with_scope": 1,
        "in_with_isolation_scope": 1,
    }

    current_scope_after.set_tag("after_current_scope", 1)

    assert isolation_scope_after._tags == {
        "before_isolation_scope": 1,
        "after_isolation_scope": 1,
    }
    assert current_scope_after._tags == {
        "before_current_scope": 1,
        "after_current_scope": 1,
    }
    assert custom_isolation_scope._tags == {
        "before_custom_isolation_scope": 1,
        "in_with_scope": 1,
        "in_with_isolation_scope": 1,
    }


def test_with_new_scope():
    original_current_scope = Scope.get_current_scope()
    original_isolation_scope = Scope.get_isolation_scope()

    with new_scope() as scope:
        assert scope._type == ScopeType.CURRENT

        in_with_current_scope = Scope.get_current_scope()
        in_with_isolation_scope = Scope.get_isolation_scope()

        assert scope is in_with_current_scope
        assert in_with_current_scope is not original_current_scope
        assert in_with_isolation_scope is original_isolation_scope

    after_with_current_scope = Scope.get_current_scope()
    after_with_isolation_scope = Scope.get_isolation_scope()
    assert after_with_current_scope is original_current_scope
    assert after_with_isolation_scope is original_isolation_scope


def test_with_new_scope_data():
    """
    When doing `with new_scope()` the current scope is forked but the isolation
    scope stays untouched.
    """
    isolation_scope_before = Scope.get_isolation_scope()
    current_scope_before = Scope.get_current_scope()

    isolation_scope_before.set_tag("before_isolation_scope", 1)
    current_scope_before.set_tag("before_current_scope", 1)

    with new_scope() as scope:
        assert scope._type == ScopeType.CURRENT

        isolation_scope_in = Scope.get_isolation_scope()
        current_scope_in = Scope.get_current_scope()

        assert isolation_scope_in._tags == {"before_isolation_scope": 1}
        assert current_scope_in._tags == {"before_current_scope": 1}
        assert scope._tags == {"before_current_scope": 1}

        scope.set_tag("in_with_scope", 1)

        assert isolation_scope_in._tags == {"before_isolation_scope": 1}
        assert current_scope_in._tags == {"before_current_scope": 1, "in_with_scope": 1}
        assert scope._tags == {"before_current_scope": 1, "in_with_scope": 1}

        isolation_scope_in.set_tag("in_with_isolation_scope", 1)

        assert isolation_scope_in._tags == {
            "before_isolation_scope": 1,
            "in_with_isolation_scope": 1,
        }
        assert current_scope_in._tags == {"before_current_scope": 1, "in_with_scope": 1}
        assert scope._tags == {"before_current_scope": 1, "in_with_scope": 1}

        current_scope_in.set_tag("in_with_current_scope", 1)

        assert isolation_scope_in._tags == {
            "before_isolation_scope": 1,
            "in_with_isolation_scope": 1,
        }
        assert current_scope_in._tags == {
            "before_current_scope": 1,
            "in_with_scope": 1,
            "in_with_current_scope": 1,
        }
        assert scope._tags == {
            "before_current_scope": 1,
            "in_with_scope": 1,
            "in_with_current_scope": 1,
        }

    isolation_scope_after = Scope.get_isolation_scope()
    current_scope_after = Scope.get_current_scope()

    isolation_scope_after.set_tag("after_isolation_scope", 1)

    assert isolation_scope_after._tags == {
        "before_isolation_scope": 1,
        "in_with_isolation_scope": 1,
        "after_isolation_scope": 1,
    }
    assert current_scope_after._tags == {"before_current_scope": 1}

    current_scope_after.set_tag("after_current_scope", 1)

    assert isolation_scope_after._tags == {
        "before_isolation_scope": 1,
        "in_with_isolation_scope": 1,
        "after_isolation_scope": 1,
    }
    assert current_scope_after._tags == {
        "before_current_scope": 1,
        "after_current_scope": 1,
    }


def test_with_use_scope_data():
    isolation_scope_before = Scope.get_isolation_scope()
    current_scope_before = Scope.get_current_scope()
    custom_current_scope = Scope()

    isolation_scope_before.set_tag("before_isolation_scope", 1)
    current_scope_before.set_tag("before_current_scope", 1)
    custom_current_scope.set_tag("before_custom_current_scope", 1)

    with use_scope(custom_current_scope) as scope:
        assert scope._type is None  # our custom scope has not type set

        isolation_scope_in = Scope.get_isolation_scope()
        current_scope_in = Scope.get_current_scope()

        assert isolation_scope_in._tags == {"before_isolation_scope": 1}
        assert current_scope_in._tags == {"before_custom_current_scope": 1}
        assert scope._tags == {"before_custom_current_scope": 1}

        scope.set_tag("in_with_scope", 1)

        assert isolation_scope_in._tags == {"before_isolation_scope": 1}
        assert current_scope_in._tags == {
            "before_custom_current_scope": 1,
            "in_with_scope": 1,
        }
        assert scope._tags == {"before_custom_current_scope": 1, "in_with_scope": 1}

        isolation_scope_in.set_tag("in_with_isolation_scope", 1)

        assert isolation_scope_in._tags == {
            "before_isolation_scope": 1,
            "in_with_isolation_scope": 1,
        }
        assert current_scope_in._tags == {
            "before_custom_current_scope": 1,
            "in_with_scope": 1,
        }
        assert scope._tags == {"before_custom_current_scope": 1, "in_with_scope": 1}

        current_scope_in.set_tag("in_with_current_scope", 1)

        assert isolation_scope_in._tags == {
            "before_isolation_scope": 1,
            "in_with_isolation_scope": 1,
        }
        assert current_scope_in._tags == {
            "before_custom_current_scope": 1,
            "in_with_scope": 1,
            "in_with_current_scope": 1,
        }
        assert scope._tags == {
            "before_custom_current_scope": 1,
            "in_with_scope": 1,
            "in_with_current_scope": 1,
        }

    assert custom_current_scope._tags == {
        "before_custom_current_scope": 1,
        "in_with_scope": 1,
        "in_with_current_scope": 1,
    }
    isolation_scope_after = Scope.get_isolation_scope()
    current_scope_after = Scope.get_current_scope()

    isolation_scope_after.set_tag("after_isolation_scope", 1)

    assert isolation_scope_after._tags == {
        "before_isolation_scope": 1,
        "after_isolation_scope": 1,
        "in_with_isolation_scope": 1,
    }
    assert current_scope_after._tags == {"before_current_scope": 1}
    assert custom_current_scope._tags == {
        "before_custom_current_scope": 1,
        "in_with_scope": 1,
        "in_with_current_scope": 1,
    }

    current_scope_after.set_tag("after_current_scope", 1)

    assert isolation_scope_after._tags == {
        "before_isolation_scope": 1,
        "in_with_isolation_scope": 1,
        "after_isolation_scope": 1,
    }
    assert current_scope_after._tags == {
        "before_current_scope": 1,
        "after_current_scope": 1,
    }
    assert custom_current_scope._tags == {
        "before_custom_current_scope": 1,
        "in_with_scope": 1,
        "in_with_current_scope": 1,
    }


def test_nested_scopes_with_tags(sentry_init, capture_envelopes):
    sentry_init(traces_sample_rate=1.0)
    envelopes = capture_envelopes()

    with sentry_sdk.isolation_scope() as scope1:
        scope1.set_tag("isolation_scope1", 1)

        with sentry_sdk.new_scope() as scope2:
            scope2.set_tag("current_scope2", 1)

            with sentry_sdk.start_transaction(name="trx") as trx:
                trx.set_tag("trx", 1)

                with sentry_sdk.start_span(op="span1") as span1:
                    span1.set_tag("a", 1)

                    with new_scope() as scope3:
                        scope3.set_tag("current_scope3", 1)

                        with sentry_sdk.start_span(op="span2") as span2:
                            span2.set_tag("b", 1)

    (envelope,) = envelopes
    transaction = envelope.items[0].get_transaction_event()

    assert transaction["tags"] == {"isolation_scope1": 1, "current_scope2": 1, "trx": 1}
    assert transaction["spans"][0]["tags"] == {"a": 1}
    assert transaction["spans"][1]["tags"] == {"b": 1}


def test_should_send_default_pii_true(sentry_init):
    sentry_init(send_default_pii=True)

    assert should_send_default_pii() is True


def test_should_send_default_pii_false(sentry_init):
    sentry_init(send_default_pii=False)

    assert should_send_default_pii() is False


def test_set_tags():
    scope = Scope()
    scope.set_tags({"tag1": "value1", "tag2": "value2"})
    event = scope.apply_to_event({}, {})

    assert event["tags"] == {"tag1": "value1", "tag2": "value2"}, "Setting tags failed"

    scope.set_tags({"tag2": "updated", "tag3": "new"})
    event = scope.apply_to_event({}, {})

    assert event["tags"] == {
        "tag1": "value1",
        "tag2": "updated",
        "tag3": "new",
    }, "Updating tags failed"

    scope.set_tags({})
    event = scope.apply_to_event({}, {})

    assert event["tags"] == {
        "tag1": "value1",
        "tag2": "updated",
        "tag3": "new",
    }, "Updating tags with empty dict changed tags"


def test_last_event_id(sentry_init):
    sentry_init(enable_tracing=True)

    assert Scope.last_event_id() is None

    sentry_sdk.capture_exception(Exception("test"))

    assert Scope.last_event_id() is not None


def test_last_event_id_transaction(sentry_init):
    sentry_init(enable_tracing=True)

    assert Scope.last_event_id() is None

    with sentry_sdk.start_transaction(name="test"):
        pass

    assert Scope.last_event_id() is None, "Transaction should not set last_event_id"


def test_last_event_id_cleared(sentry_init):
    sentry_init(enable_tracing=True)

    # Make sure last_event_id is set
    sentry_sdk.capture_exception(Exception("test"))
    assert Scope.last_event_id() is not None

    # Clearing the isolation scope should clear the last_event_id
    Scope.get_isolation_scope().clear()

    assert Scope.last_event_id() is None, "last_event_id should be cleared"
