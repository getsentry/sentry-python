import copy
from sentry_sdk.scope import Scope


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


def test_set_extras():
    scope = Scope()

    extras = {"foo": "bar", "bar": "foo"}
    scope.set_extras(extras)

    assert "foo" in scope._extras
    assert "bar" in scope._extras
    assert scope._extras["foo"] == "bar"
    assert scope._extras["bar"] == "foo"


def test_set_tags():
    scope = Scope()

    tags = {"foo": "bar", "bar": "foo"}
    scope.set_tags(tags)

    assert "foo" in scope._tags
    assert "bar" in scope._tags
    assert scope._tags["foo"] == "bar"
    assert scope._tags["bar"] == "foo"
