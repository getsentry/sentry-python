import fakeredis
from fakeredis import FakeStrictRedis

from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.utils import parse_version
import sentry_sdk


FAKEREDIS_VERSION = parse_version(fakeredis.__version__)


def test_no_cache_basic(sentry_init, capture_events):
    sentry_init(
        integrations=[
            RedisIntegration(),
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    connection = FakeStrictRedis()
    with sentry_sdk.start_transaction():
        connection.get("mycachekey")

    (event,) = events
    spans = event["spans"]
    assert len(spans) == 1
    assert spans[0]["op"] == "db.redis"


def test_cache_basic(sentry_init, capture_events):
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
    with sentry_sdk.start_transaction():
        connection.get("mycachekey")

    (event,) = events
    spans = event["spans"]
    assert len(spans) == 2
    assert spans[0]["op"] == "cache.get_item"
    assert spans[1]["op"] == "db.redis"


def test_cache_keys(sentry_init, capture_events):
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

    assert spans[1]["op"] == "cache.get_item"
    assert spans[1]["description"] == "blub"
    assert spans[2]["op"] == "db.redis"
    assert spans[2]["description"] == "GET 'blub'"

    assert spans[3]["op"] == "cache.get_item"
    assert spans[3]["description"] == "blubkeything"
    assert spans[4]["op"] == "db.redis"
    assert spans[4]["description"] == "GET 'blubkeything'"

    assert spans[5]["op"] == "db.redis"
    assert spans[5]["description"] == "GET 'bl'"


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
    with sentry_sdk.start_transaction():
        connection.get("mycachekey")
        connection.set("mycachekey", "事实胜于雄辩")
        connection.get("mycachekey")

    (event,) = events
    spans = event["spans"]

    assert len(spans) == 6

    assert spans[0]["op"] == "cache.get_item"
    assert spans[0]["description"] == "mycachekey"
    assert spans[0]["data"]["cache.key"] == "mycachekey"
    assert spans[0]["data"]["cache.hit"] == False  # noqa: E712
    assert "cache.item_size" not in spans[0]["data"]
    # very old fakeredis can not handle port.
    # only used with redis v3
    if FAKEREDIS_VERSION < (2, 7, 1):
        assert "network.peer.address" not in spans[0]["data"]
        assert "network.peer.port" not in spans[0]["data"]
    else:
        assert spans[0]["data"]["network.peer.address"] == "mycacheserver.io"
        assert spans[0]["data"]["network.peer.port"] == 6378

    assert spans[1]["op"] == "db.redis"  # we ignore db spans in this test.

    assert spans[2]["op"] == "cache.set_item"
    assert spans[2]["description"] == "mycachekey"
    assert spans[2]["data"]["cache.key"] == "mycachekey"
    assert "cache.hit" not in spans[1]["data"]
    assert spans[2]["data"]["cache.item_size"] == 18
    # very old fakeredis can not handle port.
    # only used with redis v3
    if FAKEREDIS_VERSION < (2, 7, 1):
        assert "network.peer.address" not in spans[2]["data"]
        assert "network.peer.port" not in spans[2]["data"]
    else:
        assert spans[2]["data"]["network.peer.address"] == "mycacheserver.io"
        assert spans[2]["data"]["network.peer.port"] == 6378

    assert spans[3]["op"] == "db.redis"  # we ignore db spans in this test.

    assert spans[4]["op"] == "cache.get_item"
    assert spans[4]["description"] == "mycachekey"
    assert spans[4]["data"]["cache.key"] == "mycachekey"
    assert spans[4]["data"]["cache.hit"] == True  # noqa: E712
    assert spans[4]["data"]["cache.item_size"] == 18
    # very old fakeredis can not handle port.
    # only used with redis v3
    if FAKEREDIS_VERSION < (2, 7, 1):
        assert "network.peer.address" not in spans[4]["data"]
        assert "network.peer.port" not in spans[4]["data"]
    else:
        assert spans[4]["data"]["network.peer.address"] == "mycacheserver.io"
        assert spans[4]["data"]["network.peer.port"] == 6378

    assert spans[5]["op"] == "db.redis"  # we ignore db spans in this test.
