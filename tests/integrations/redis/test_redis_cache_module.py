import uuid

import pytest

import fakeredis
from fakeredis import FakeStrictRedis

from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.integrations.redis.utils import _get_safe_key, _key_as_string
from sentry_sdk.utils import parse_version
import sentry_sdk


FAKEREDIS_VERSION = parse_version(fakeredis.__version__)


def test_no_cache_basic(sentry_init, capture_events, render_span_tree):
    sentry_init(
        integrations=[
            RedisIntegration(),
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    connection = FakeStrictRedis()
    with sentry_sdk.start_span(name="cache"):
        connection.get("mycachekey")

    (event,) = events
    assert (
        render_span_tree(event)
        == """\
- op="cache": description=null
  - op="db.redis": description="GET 'mycachekey'"\
"""
    )


def test_cache_basic(sentry_init, capture_events, render_span_tree):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["mycache"],
            ),
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    connection = FakeStrictRedis()
    with sentry_sdk.start_span(name="cache"):
        connection.hget("mycachekey", "myfield")
        connection.get("mycachekey")
        connection.set("mycachekey1", "bla")
        connection.setex("mycachekey2", 10, "blub")
        connection.mget("mycachekey1", "mycachekey2")

    (event,) = events
    # no cache support for HGET command
    assert (
        render_span_tree(event)
        == """\
- op="cache": description=null
  - op="db.redis": description="HGET 'mycachekey' [Filtered]"
  - op="cache.get": description="mycachekey"
    - op="db.redis": description="GET 'mycachekey'"
  - op="cache.put": description="mycachekey1"
    - op="db.redis": description="SET 'mycachekey1' [Filtered]"
  - op="cache.put": description="mycachekey2"
    - op="db.redis": description="SETEX 'mycachekey2' [Filtered] [Filtered]"
  - op="cache.get": description="mycachekey1, mycachekey2"
    - op="db.redis": description="MGET 'mycachekey1' [Filtered]"\
"""
    )


def test_cache_keys(sentry_init, capture_events, render_span_tree):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["bla", "blub"],
            ),
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    connection = FakeStrictRedis()
    with sentry_sdk.start_span(name="cache"):
        connection.get("somethingelse")
        connection.get("blub")
        connection.get("blubkeything")
        connection.get("bl")

    (event,) = events
    assert (
        render_span_tree(event)
        == """\
- op="cache": description=null
  - op="db.redis": description="GET 'somethingelse'"
  - op="cache.get": description="blub"
    - op="db.redis": description="GET 'blub'"
  - op="cache.get": description="blubkeything"
    - op="db.redis": description="GET 'blubkeything'"
  - op="db.redis": description="GET 'bl'"\
"""
    )


def test_cache_data(sentry_init, capture_events):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["mycache"],
            ),
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    connection = FakeStrictRedis(host="mycacheserver.io", port=6378)
    with sentry_sdk.start_span(name="cache"):
        connection.get("mycachekey")
        connection.set("mycachekey", "事实胜于雄辩")
        connection.get("mycachekey")

    (event,) = events
    spans = sorted(event["spans"], key=lambda x: x["start_timestamp"])

    assert len(spans) == 6

    assert spans[0]["op"] == "cache.get"
    assert spans[0]["description"] == "mycachekey"
    assert spans[0]["data"]["cache.key"] == [
        "mycachekey",
    ]
    assert spans[0]["data"]["cache.hit"] == False  # noqa: E712
    assert "cache.item_size" not in spans[0]["data"]
    # very old fakeredis can not handle port and/or host.
    # only applicable for Redis v3
    if FAKEREDIS_VERSION <= (2, 7, 1):
        assert "network.peer.port" not in spans[0]["data"]
    else:
        assert spans[0]["data"]["network.peer.port"] == 6378
    if FAKEREDIS_VERSION <= (1, 7, 1):
        assert "network.peer.address" not in spans[0]["data"]
    else:
        assert spans[0]["data"]["network.peer.address"] == "mycacheserver.io"

    assert spans[1]["op"] == "db.redis"  # we ignore db spans in this test.

    assert spans[2]["op"] == "cache.put"
    assert spans[2]["description"] == "mycachekey"
    assert spans[2]["data"]["cache.key"] == [
        "mycachekey",
    ]
    assert "cache.hit" not in spans[1]["data"]
    assert spans[2]["data"]["cache.item_size"] == 18
    # very old fakeredis can not handle port.
    # only used with redis v3
    if FAKEREDIS_VERSION <= (2, 7, 1):
        assert "network.peer.port" not in spans[2]["data"]
    else:
        assert spans[2]["data"]["network.peer.port"] == 6378
    if FAKEREDIS_VERSION <= (1, 7, 1):
        assert "network.peer.address" not in spans[2]["data"]
    else:
        assert spans[2]["data"]["network.peer.address"] == "mycacheserver.io"

    assert spans[3]["op"] == "db.redis"  # we ignore db spans in this test.

    assert spans[4]["op"] == "cache.get"
    assert spans[4]["description"] == "mycachekey"
    assert spans[4]["data"]["cache.key"] == [
        "mycachekey",
    ]
    assert spans[4]["data"]["cache.hit"] == True  # noqa: E712
    assert spans[4]["data"]["cache.item_size"] == 18
    # very old fakeredis can not handle port.
    # only used with redis v3
    if FAKEREDIS_VERSION <= (2, 7, 1):
        assert "network.peer.port" not in spans[4]["data"]
    else:
        assert spans[4]["data"]["network.peer.port"] == 6378
    if FAKEREDIS_VERSION <= (1, 7, 1):
        assert "network.peer.address" not in spans[4]["data"]
    else:
        assert spans[4]["data"]["network.peer.address"] == "mycacheserver.io"

    assert spans[5]["op"] == "db.redis"  # we ignore db spans in this test.


def test_cache_prefixes(sentry_init, capture_events):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["yes"],
            ),
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    connection = FakeStrictRedis()
    with sentry_sdk.start_span(name="cache"):
        connection.mget("yes", "no")
        connection.mget("no", 1, "yes")
        connection.mget("no", "yes.1", "yes.2")
        connection.mget("no.1", "no.2", "no.3")
        connection.mget("no.1", "no.2", "no.actually.yes")
        connection.mget(b"no.3", b"yes.5")
        connection.mget(uuid.uuid4().bytes)
        connection.mget(uuid.uuid4().bytes, "yes")

    (event,) = events

    spans = sorted(event["spans"], key=lambda x: x["start_timestamp"])
    assert len(spans) == 13  # 8 db spans + 5 cache spans

    cache_spans = [span for span in spans if span["op"] == "cache.get"]
    assert len(cache_spans) == 5

    assert cache_spans[0]["description"] == "yes, no"
    assert cache_spans[1]["description"] == "no, 1, yes"
    assert cache_spans[2]["description"] == "no, yes.1, yes.2"
    assert cache_spans[3]["description"] == "no.3, yes.5"
    assert cache_spans[4]["description"] == ", yes"


@pytest.mark.parametrize(
    "method_name,args,kwargs,expected_key",
    [
        (None, None, None, None),
        ("", None, None, None),
        ("set", ["bla", "valuebla"], None, ("bla",)),
        ("setex", ["bla", 10, "valuebla"], None, ("bla",)),
        ("get", ["bla"], None, ("bla",)),
        ("mget", ["bla", "blub", "foo"], None, ("bla", "blub", "foo")),
        ("set", [b"bla", "valuebla"], None, (b"bla",)),
        ("setex", [b"bla", 10, "valuebla"], None, (b"bla",)),
        ("get", [b"bla"], None, (b"bla",)),
        ("mget", [b"bla", "blub", "foo"], None, (b"bla", "blub", "foo")),
        ("not-important", None, {"something": "bla"}, None),
        ("not-important", None, {"key": None}, None),
        ("not-important", None, {"key": "bla"}, ("bla",)),
        ("not-important", None, {"key": b"bla"}, (b"bla",)),
        ("not-important", None, {"key": []}, None),
        (
            "not-important",
            None,
            {
                "key": [
                    "bla",
                ]
            },
            ("bla",),
        ),
        (
            "not-important",
            None,
            {"key": [b"bla", "blub", "foo"]},
            (b"bla", "blub", "foo"),
        ),
        (
            "not-important",
            None,
            {"key": b"\x00c\x0f\xeaC\xe1L\x1c\xbff\xcb\xcc\xc1\xed\xc6\t"},
            (b"\x00c\x0f\xeaC\xe1L\x1c\xbff\xcb\xcc\xc1\xed\xc6\t",),
        ),
        (
            "get",
            [b"\x00c\x0f\xeaC\xe1L\x1c\xbff\xcb\xcc\xc1\xed\xc6\t"],
            None,
            (b"\x00c\x0f\xeaC\xe1L\x1c\xbff\xcb\xcc\xc1\xed\xc6\t",),
        ),
        (
            "get",
            [123],
            None,
            (123,),
        ),
    ],
)
def test_get_safe_key(method_name, args, kwargs, expected_key):
    assert _get_safe_key(method_name, args, kwargs) == expected_key


@pytest.mark.parametrize(
    "key,expected_key",
    [
        (None, ""),
        (("bla",), "bla"),
        (("bla", "blub", "foo"), "bla, blub, foo"),
        ((b"bla",), "bla"),
        ((b"bla", "blub", "foo"), "bla, blub, foo"),
        (
            [
                "bla",
            ],
            "bla",
        ),
        (["bla", "blub", "foo"], "bla, blub, foo"),
        ([uuid.uuid4().bytes], ""),
        ({"key1": 1, "key2": 2}, "key1, key2"),
        (1, "1"),
        ([1, 2, 3, b"hello"], "1, 2, 3, hello"),
    ],
)
def test_key_as_string(key, expected_key):
    assert _key_as_string(key) == expected_key
