import sys
import json

from sentry_sdk.utils import (
    AnnotatedValue,
    convert_types,
    event_from_exception,
    flatten_metadata,
    strip_databag,
    strip_event_mut,
)


def test_flatten_metadata():
    assert flatten_metadata({"foo": u"bar"}) == {"foo": u"bar"}
    assert flatten_metadata({"foo": ["bar"]}) == {"foo": [u"bar"]}
    assert flatten_metadata({"foo": [AnnotatedValue("bar", u"meta")]}) == {
        "foo": [u"bar"],
        "_meta": {"foo": {"0": {"": u"meta"}}},
    }


def test_strip_databag():
    d = strip_databag({"foo": u"a" * 2000})
    assert len(d["foo"].value) == 512


def test_strip_exception_vars():
    try:
        a = "A" * 16000  # noqa
        1 / 0
    except Exception:
        event, _ = event_from_exception(sys.exc_info())

    assert len(json.dumps(event)) > 10000
    strip_event_mut(event)
    event = flatten_metadata(event)
    event = convert_types(event)
    assert len(json.dumps(event)) < 10000
