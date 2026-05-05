import uuid

import pytest

try:
    import fakeredis
    from fakeredis.aioredis import FakeRedis as FakeRedisAsync
except ModuleNotFoundError:
    FakeRedisAsync = None

if FakeRedisAsync is None:
    pytest.skip(
        "Skipping tests because fakeredis.aioredis not available",
        allow_module_level=True,
    )

from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.utils import parse_version
import sentry_sdk


FAKEREDIS_VERSION = parse_version(fakeredis.__version__)


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.asyncio
async def test_no_cache_basic(
    sentry_init, capture_events, capture_items, span_streaming
):
    sentry_init(
        integrations=[
            RedisIntegration(),
        ],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    connection = FakeRedisAsync()

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            await connection.get("myasynccachekey")
        sentry_sdk.flush()

        assert len(items) == 2
        db_span, parent_span = items[0].payload, items[1].payload
        assert parent_span["name"] == "custom parent"
        assert db_span["attributes"]["sentry.op"] == "db.redis"
    else:
        events = capture_events()
        with sentry_sdk.start_transaction():
            await connection.get("myasynccachekey")

        (event,) = events
        spans = event["spans"]
        assert len(spans) == 1
        assert spans[0]["op"] == "db.redis"


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.asyncio
async def test_cache_basic(sentry_init, capture_events, capture_items, span_streaming):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["myasynccache"],
            ),
        ],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    connection = FakeRedisAsync()

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            await connection.get("myasynccachekey")
        sentry_sdk.flush()

        assert len(items) == 3
        db_span, cache_span, parent_span = [item.payload for item in items]
        assert parent_span["name"] == "custom parent"
        assert db_span["attributes"]["sentry.op"] == "db.redis"
        assert cache_span["attributes"]["sentry.op"] == "cache.get"
    else:
        events = capture_events()
        with sentry_sdk.start_transaction():
            await connection.get("myasynccachekey")

        (event,) = events
        spans = event["spans"]
        assert len(spans) == 2

        assert spans[0]["op"] == "cache.get"
        assert spans[1]["op"] == "db.redis"


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.asyncio
async def test_cache_keys(sentry_init, capture_events, capture_items, span_streaming):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["abla", "ablub"],
            ),
        ],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    connection = FakeRedisAsync()

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            await connection.get("asomethingelse")
            await connection.get("ablub")
            await connection.get("ablubkeything")
            await connection.get("abl")
        sentry_sdk.flush()

        assert len(items) == 7
        payloads = [item.payload for item in items]

        # asomethingelse: db only
        assert payloads[0]["attributes"]["sentry.op"] == "db.redis"
        assert payloads[0]["name"] == "GET 'asomethingelse'"

        # ablub: db then cache.get
        assert payloads[1]["attributes"]["sentry.op"] == "db.redis"
        assert payloads[1]["name"] == "GET 'ablub'"
        assert payloads[2]["attributes"]["sentry.op"] == "cache.get"
        assert payloads[2]["name"] == "ablub"

        # ablubkeything: db then cache.get
        assert payloads[3]["attributes"]["sentry.op"] == "db.redis"
        assert payloads[3]["name"] == "GET 'ablubkeything'"
        assert payloads[4]["attributes"]["sentry.op"] == "cache.get"
        assert payloads[4]["name"] == "ablubkeything"

        # abl: db only (no prefix match)
        assert payloads[5]["attributes"]["sentry.op"] == "db.redis"
        assert payloads[5]["name"] == "GET 'abl'"

        assert payloads[6]["name"] == "custom parent"
    else:
        events = capture_events()
        with sentry_sdk.start_transaction():
            await connection.get("asomethingelse")
            await connection.get("ablub")
            await connection.get("ablubkeything")
            await connection.get("abl")

        (event,) = events
        spans = event["spans"]
        assert len(spans) == 6
        assert spans[0]["op"] == "db.redis"
        assert spans[0]["description"] == "GET 'asomethingelse'"

        assert spans[1]["op"] == "cache.get"
        assert spans[1]["description"] == "ablub"
        assert spans[2]["op"] == "db.redis"
        assert spans[2]["description"] == "GET 'ablub'"

        assert spans[3]["op"] == "cache.get"
        assert spans[3]["description"] == "ablubkeything"
        assert spans[4]["op"] == "db.redis"
        assert spans[4]["description"] == "GET 'ablubkeything'"

        assert spans[5]["op"] == "db.redis"
        assert spans[5]["description"] == "GET 'abl'"


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.asyncio
async def test_cache_data(sentry_init, capture_events, capture_items, span_streaming):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["myasynccache"],
            ),
        ],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    # Use a unique host per parametrized run so fakeredis (which shares state
    # keyed by host:port) doesn't leak the SET from a prior run into this one.
    host = f"mycacheserver-{uuid.uuid4().hex}.io"
    connection = FakeRedisAsync(host=host, port=6378)

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            await connection.get("myasynccachekey")
            await connection.set("myasynccachekey", "事实胜于雄辩")
            await connection.get("myasynccachekey")
        sentry_sdk.flush()

        assert len(items) == 7
        payloads = [item.payload for item in items]

        # First get (miss)
        assert payloads[0]["attributes"]["sentry.op"] == "db.redis"
        cache_get_miss = payloads[1]
        assert cache_get_miss["attributes"]["sentry.op"] == "cache.get"
        assert cache_get_miss["name"] == "myasynccachekey"
        assert cache_get_miss["attributes"]["cache.key"] == ["myasynccachekey"]
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
        assert cache_put["name"] == "myasynccachekey"
        assert cache_put["attributes"]["cache.key"] == ["myasynccachekey"]
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
        assert cache_get_hit["attributes"]["cache.key"] == ["myasynccachekey"]
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
            await connection.get("myasynccachekey")
            await connection.set("myasynccachekey", "事实胜于雄辩")
            await connection.get("myasynccachekey")

        (event,) = events
        spans = event["spans"]

        assert len(spans) == 6

        assert spans[0]["op"] == "cache.get"
        assert spans[0]["description"] == "myasynccachekey"
        assert spans[0]["data"]["cache.key"] == [
            "myasynccachekey",
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
        assert spans[2]["description"] == "myasynccachekey"
        assert spans[2]["data"]["cache.key"] == [
            "myasynccachekey",
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
        assert spans[4]["description"] == "myasynccachekey"
        assert spans[4]["data"]["cache.key"] == [
            "myasynccachekey",
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
