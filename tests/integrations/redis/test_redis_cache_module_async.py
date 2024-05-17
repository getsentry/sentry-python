import pytest

try:
    import fakeredis
    from fakeredis.aioredis import FakeRedis as FakeRedisAsync
except ModuleNotFoundError:
    FakeRedisAsync = None

if FakeRedisAsync is None:
    pytest.skip("Skipping tests because fakeredis.aioredis not available", allow_module_level=True)

from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.utils import parse_version
import sentry_sdk


FAKEREDIS_VERSION = parse_version(fakeredis.__version__)


@pytest.mark.asyncio
async def test_no_cache_basic(sentry_init, capture_events):
    sentry_init(
        integrations=[
            RedisIntegration(),
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    connection = FakeRedisAsync()
    with sentry_sdk.start_transaction():
        await connection.get("myasynccachekey")

    (event,) = events
    spans = event["spans"]
    assert len(spans) == 1
    assert spans[0]["op"] == "db.redis"


@pytest.mark.asyncio
async def test_cache_basic(sentry_init, capture_events):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["myasynccache"],
            ),
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    connection = FakeRedisAsync()
    with sentry_sdk.start_transaction():
        await connection.get("myasynccachekey")

    (event,) = events
    spans = event["spans"]
    assert len(spans) == 2
    
    assert spans[0]["op"] == "cache.get_item"
    assert spans[1]["op"] == "db.redis"


@pytest.mark.asyncio
async def test_cache_keys(sentry_init, capture_events):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["abla", "ablub"],
            ),
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    connection = FakeRedisAsync()
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

    assert spans[1]["op"] == "cache.get_item"
    assert spans[1]["description"] == "ablub"
    assert spans[2]["op"] == "db.redis"
    assert spans[2]["description"] == "GET 'ablub'"

    assert spans[3]["op"] == "cache.get_item"
    assert spans[3]["description"] == "ablubkeything"
    assert spans[4]["op"] == "db.redis"
    assert spans[4]["description"] == "GET 'ablubkeything'"

    assert spans[5]["op"] == "db.redis"
    assert spans[5]["description"] == "GET 'abl'"


@pytest.mark.asyncio
async def test_cache_data(sentry_init, capture_events):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["myasynccache"],
            ),
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    connection = FakeRedisAsync(host="mycacheserver.io", port=6378)
    with sentry_sdk.start_transaction():
        await connection.get("myasynccachekey")
        await connection.set("myasynccachekey", "事实胜于雄辩")
        await connection.get("myasynccachekey")

    (event,) = events
    spans = event["spans"]

    assert len(spans) == 6

    assert spans[0]["op"] == "cache.get_item"
    assert spans[0]["description"] == "myasynccachekey"
    assert spans[0]["data"]["cache.key"] == "myasynccachekey"
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

    assert spans[2]["op"] == "cache.set_item"
    assert spans[2]["description"] == "myasynccachekey"
    assert spans[2]["data"]["cache.key"] == "myasynccachekey"
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

    assert spans[4]["op"] == "cache.get_item"
    assert spans[4]["description"] == "myasynccachekey"
    assert spans[4]["data"]["cache.key"] == "myasynccachekey"
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
