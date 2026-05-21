import uuid

import fakeredis
import pytest
from fakeredis import FakeStrictRedis

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.integrations.redis.utils import _get_safe_key, _key_as_string
from sentry_sdk.utils import parse_version

FAKEREDIS_VERSION = parse_version(fakeredis.__version__)


@pytest.mark.parametrize("span_streaming", [True, False])
def test_no_cache_basic(sentry_init, capture_events, capture_items, span_streaming):
    sentry_init(
        integrations=[
            RedisIntegration(),
        ],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    connection = FakeStrictRedis()

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            connection.get("mycachekey")
        sentry_sdk.flush()

        assert len(items) == 2
        db_span, parent_span = items[0].payload, items[1].payload
        assert parent_span["name"] == "custom parent"
        assert db_span["attributes"]["sentry.op"] == "db.redis"
    else:
        events = capture_events()
        with sentry_sdk.start_transaction():
            connection.get("mycachekey")

        (event,) = events
        spans = event["spans"]
        assert len(spans) == 1
        assert spans[0]["op"] == "db.redis"


@pytest.mark.parametrize("span_streaming", [True, False])
def test_cache_basic(sentry_init, capture_events, capture_items, span_streaming):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["mycache"],
            ),
        ],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    connection = FakeStrictRedis()

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            connection.hget("mycachekey", "myfield")
            connection.get("mycachekey")
            connection.set("mycachekey1", "bla")
            connection.setex("mycachekey2", 10, "blub")
            connection.mget("mycachekey1", "mycachekey2")
        sentry_sdk.flush()

        # Close order: db spans close before their wrapping cache span,
        # and the "custom parent" segment closes last.
        assert len(items) == 10
        payloads = [item.payload for item in items]

        # hget: db only (HGET is not a cache command)
        assert payloads[0]["attributes"]["sentry.op"] == "db.redis"
        assert payloads[0]["attributes"][SPANDATA.DB_OPERATION_NAME] == "HGET"

        # get: db then cache.get
        assert payloads[1]["attributes"]["sentry.op"] == "db.redis"
        assert payloads[1]["attributes"][SPANDATA.DB_OPERATION_NAME] == "GET"
        assert payloads[2]["attributes"]["sentry.op"] == "cache.get"

        # set: db then cache.put
        assert payloads[3]["attributes"]["sentry.op"] == "db.redis"
        assert payloads[3]["attributes"][SPANDATA.DB_OPERATION_NAME] == "SET"
        assert payloads[4]["attributes"]["sentry.op"] == "cache.put"

        # setex: db then cache.put
        assert payloads[5]["attributes"]["sentry.op"] == "db.redis"
        assert payloads[5]["attributes"][SPANDATA.DB_OPERATION_NAME] == "SETEX"
        assert payloads[6]["attributes"]["sentry.op"] == "cache.put"

        # mget: db then cache.get
        assert payloads[7]["attributes"]["sentry.op"] == "db.redis"
        assert payloads[7]["attributes"][SPANDATA.DB_OPERATION_NAME] == "MGET"
        assert payloads[8]["attributes"]["sentry.op"] == "cache.get"

        assert payloads[9]["name"] == "custom parent"
    else:
        events = capture_events()
        with sentry_sdk.start_transaction():
            connection.hget("mycachekey", "myfield")
            connection.get("mycachekey")
            connection.set("mycachekey1", "bla")
            connection.setex("mycachekey2", 10, "blub")
            connection.mget("mycachekey1", "mycachekey2")

        (event,) = events
        spans = event["spans"]
        assert len(spans) == 9

        # no cache support for hget command
        assert spans[0]["op"] == "db.redis"
        assert spans[0]["tags"]["redis.command"] == "HGET"

        assert spans[1]["op"] == "cache.get"
        assert spans[2]["op"] == "db.redis"
        assert spans[2]["tags"]["redis.command"] == "GET"

        assert spans[3]["op"] == "cache.put"
        assert spans[4]["op"] == "db.redis"
        assert spans[4]["tags"]["redis.command"] == "SET"

        assert spans[5]["op"] == "cache.put"
        assert spans[6]["op"] == "db.redis"
        assert spans[6]["tags"]["redis.command"] == "SETEX"

        assert spans[7]["op"] == "cache.get"
        assert spans[8]["op"] == "db.redis"
        assert spans[8]["tags"]["redis.command"] == "MGET"


@pytest.mark.parametrize("span_streaming", [True, False])
def test_cache_keys(sentry_init, capture_events, capture_items, span_streaming):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["bla", "blub"],
            ),
        ],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    connection = FakeStrictRedis()

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            connection.get("somethingelse")
            connection.get("blub")
            connection.get("blubkeything")
            connection.get("bl")
        sentry_sdk.flush()

        assert len(items) == 7
        payloads = [item.payload for item in items]

        # somethingelse: db only
        assert payloads[0]["attributes"]["sentry.op"] == "db.redis"
        assert payloads[0]["name"] == "GET 'somethingelse'"

        # blub: db then cache.get
        assert payloads[1]["attributes"]["sentry.op"] == "db.redis"
        assert payloads[1]["name"] == "GET 'blub'"
        assert payloads[2]["attributes"]["sentry.op"] == "cache.get"
        assert payloads[2]["name"] == "blub"

        # blubkeything: db then cache.get
        assert payloads[3]["attributes"]["sentry.op"] == "db.redis"
        assert payloads[3]["name"] == "GET 'blubkeything'"
        assert payloads[4]["attributes"]["sentry.op"] == "cache.get"
        assert payloads[4]["name"] == "blubkeything"

        # bl: db only (no prefix match)
        assert payloads[5]["attributes"]["sentry.op"] == "db.redis"
        assert payloads[5]["name"] == "GET 'bl'"

        assert payloads[6]["name"] == "custom parent"
    else:
        events = capture_events()
        with sentry_sdk.start_transaction():
            connection.get("somethingelse")
            connection.get("blub")
            connection.get("blubkeything")
            connection.get("bl")

        (event,) = events
        spans = event["spans"]
        assert len(spans) == 6
        assert spans[0]["op"] == "db.redis"
        assert spans[0]["description"] == "GET 'somethingelse'"

        assert spans[1]["op"] == "cache.get"
        assert spans[1]["description"] == "blub"
        assert spans[2]["op"] == "db.redis"
        assert spans[2]["description"] == "GET 'blub'"

        assert spans[3]["op"] == "cache.get"
        assert spans[3]["description"] == "blubkeything"
        assert spans[4]["op"] == "db.redis"
        assert spans[4]["description"] == "GET 'blubkeything'"

        assert spans[5]["op"] == "db.redis"
        assert spans[5]["description"] == "GET 'bl'"


@pytest.mark.parametrize("span_streaming", [True, False])
def test_cache_data(sentry_init, capture_events, capture_items, span_streaming):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["mycache"],
            ),
        ],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    # Use a unique host per parametrized run so fakeredis (which shares state
    # keyed by host:port) doesn't leak the SET from a prior run into this one.
    host = f"mycacheserver-{uuid.uuid4().hex}.io"
    connection = FakeStrictRedis(host=host, port=6378)

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            connection.get("mycachekey")
            connection.set("mycachekey", "事实胜于雄辩")
            connection.get("mycachekey")
        sentry_sdk.flush()

        # Close order: db then cache for each command, then parent
        assert len(items) == 7
        payloads = [item.payload for item in items]

        # First get (miss)
        assert payloads[0]["attributes"]["sentry.op"] == "db.redis"
        cache_get_miss = payloads[1]
        assert cache_get_miss["attributes"]["sentry.op"] == "cache.get"
        assert cache_get_miss["name"] == "mycachekey"
        assert cache_get_miss["attributes"]["cache.key"] == ["mycachekey"]
        assert cache_get_miss["attributes"]["cache.hit"] is False
        assert "cache.item_size" not in cache_get_miss["attributes"]
        if FAKEREDIS_VERSION <= (2, 7, 1):
            assert "network.peer.port" not in cache_get_miss["attributes"]
        else:
            assert cache_get_miss["attributes"]["network.peer.port"] == 6378
        if FAKEREDIS_VERSION <= (1, 7, 1):
            assert "network.peer.address" not in cache_get_miss["attributes"]
        else:
            assert cache_get_miss["attributes"]["network.peer.address"] == host

        # Set
        assert payloads[2]["attributes"]["sentry.op"] == "db.redis"
        cache_put = payloads[3]
        assert cache_put["attributes"]["sentry.op"] == "cache.put"
        assert cache_put["name"] == "mycachekey"
        assert cache_put["attributes"]["cache.key"] == ["mycachekey"]
        assert "cache.hit" not in cache_put["attributes"]
        assert cache_put["attributes"]["cache.item_size"] == 18
        if FAKEREDIS_VERSION <= (2, 7, 1):
            assert "network.peer.port" not in cache_put["attributes"]
        else:
            assert cache_put["attributes"]["network.peer.port"] == 6378
        if FAKEREDIS_VERSION <= (1, 7, 1):
            assert "network.peer.address" not in cache_put["attributes"]
        else:
            assert cache_put["attributes"]["network.peer.address"] == host

        # Second get (hit)
        assert payloads[4]["attributes"]["sentry.op"] == "db.redis"
        cache_get_hit = payloads[5]
        assert cache_get_hit["attributes"]["sentry.op"] == "cache.get"
        assert cache_get_hit["attributes"]["cache.key"] == ["mycachekey"]
        assert cache_get_hit["attributes"]["cache.hit"] is True
        assert cache_get_hit["attributes"]["cache.item_size"] == 18
        if FAKEREDIS_VERSION <= (2, 7, 1):
            assert "network.peer.port" not in cache_get_hit["attributes"]
        else:
            assert cache_get_hit["attributes"]["network.peer.port"] == 6378
        if FAKEREDIS_VERSION <= (1, 7, 1):
            assert "network.peer.address" not in cache_get_hit["attributes"]
        else:
            assert cache_get_hit["attributes"]["network.peer.address"] == host

        assert payloads[6]["name"] == "custom parent"
    else:
        events = capture_events()
        with sentry_sdk.start_transaction():
            connection.get("mycachekey")
            connection.set("mycachekey", "事实胜于雄辩")
            connection.get("mycachekey")

        (event,) = events
        spans = event["spans"]

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
            assert spans[0]["data"]["network.peer.address"] == host

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
            assert spans[2]["data"]["network.peer.address"] == host

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
            assert spans[4]["data"]["network.peer.address"] == host

        assert spans[5]["op"] == "db.redis"  # we ignore db spans in this test.


@pytest.mark.parametrize("span_streaming", [True, False])
def test_cache_prefixes(sentry_init, capture_events, capture_items, span_streaming):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["yes"],
            ),
        ],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    connection = FakeStrictRedis()

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            connection.mget("yes", "no")
            connection.mget("no", 1, "yes")
            connection.mget("no", "yes.1", "yes.2")
            connection.mget("no.1", "no.2", "no.3")
            connection.mget("no.1", "no.2", "no.actually.yes")
            connection.mget(b"no.3", b"yes.5")
            connection.mget(uuid.uuid4().bytes)
            connection.mget(uuid.uuid4().bytes, "yes")
        sentry_sdk.flush()

        # 8 db spans + 5 cache spans + 1 parent
        assert len(items) == 14
        payloads = [item.payload for item in items]
        assert payloads[-1]["name"] == "custom parent"

        cache_spans = [
            p for p in payloads if p["attributes"].get("sentry.op") == "cache.get"
        ]
        assert len(cache_spans) == 5

        assert cache_spans[0]["name"] == "yes, no"
        assert cache_spans[1]["name"] == "no, 1, yes"
        assert cache_spans[2]["name"] == "no, yes.1, yes.2"
        assert cache_spans[3]["name"] == "no.3, yes.5"
        assert cache_spans[4]["name"] == ", yes"
    else:
        events = capture_events()
        with sentry_sdk.start_transaction():
            connection.mget("yes", "no")
            connection.mget("no", 1, "yes")
            connection.mget("no", "yes.1", "yes.2")
            connection.mget("no.1", "no.2", "no.3")
            connection.mget("no.1", "no.2", "no.actually.yes")
            connection.mget(b"no.3", b"yes.5")
            connection.mget(uuid.uuid4().bytes)
            connection.mget(uuid.uuid4().bytes, "yes")

        (event,) = events

        spans = event["spans"]
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
