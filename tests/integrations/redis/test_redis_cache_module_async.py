import uuid

import pytest

pytest.importorskip(
    "fakeredis.aioredis",
    reason="Skipping tests because fakeredis.aioredis not available",
)

import fakeredis
from fakeredis.aioredis import FakeRedis as FakeRedisAsync
from sentry_conventions.attributes import ATTRIBUTE_NAMES

import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.utils import parse_version

FAKEREDIS_VERSION = parse_version(fakeredis.__version__)


@pytest.mark.asyncio
async def test_no_cache_basic(sentry_init, capture_items):
    sentry_init(
        integrations=[
            RedisIntegration(),
        ],
        traces_sample_rate=1.0,
    )

    connection = FakeRedisAsync()

    items = capture_items("span")
    with sentry_sdk.start_span(name="custom parent"):
        await connection.get("myasynccachekey")
    sentry_sdk.flush()

    assert len(items) == 2
    db_span, parent_span = items[0].payload, items[1].payload
    assert parent_span["name"] == "custom parent"
    assert db_span["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == OP.DB_QUERY


@pytest.mark.asyncio
async def test_cache_basic(sentry_init, capture_items):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["myasynccache"],
            ),
        ],
        traces_sample_rate=1.0,
    )

    connection = FakeRedisAsync()

    items = capture_items("span")
    with sentry_sdk.start_span(name="custom parent"):
        await connection.get("myasynccachekey")
    sentry_sdk.flush()

    assert len(items) == 3
    db_span, cache_span, parent_span = [item.payload for item in items]
    assert parent_span["name"] == "custom parent"
    assert db_span["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == OP.DB_QUERY
    assert cache_span["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == "cache.get"
    assert cache_span["attributes"][ATTRIBUTE_NAMES.CACHE_KEY] == ["myasynccachekey"]


@pytest.mark.asyncio
async def test_cache_keys(sentry_init, capture_items):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["abla", "ablub"],
            ),
        ],
        traces_sample_rate=1.0,
    )

    connection = FakeRedisAsync()

    items = capture_items("span")
    with sentry_sdk.start_span(name="custom parent"):
        await connection.get("asomethingelse")
        await connection.get("ablub")
        await connection.get("ablubkeything")
        await connection.get("abl")
    sentry_sdk.flush()

    assert len(items) == 7
    payloads = [item.payload for item in items]

    # asomethingelse: db only
    assert payloads[0]["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == OP.DB_QUERY
    assert payloads[0]["name"] == "GET 'asomethingelse'"

    # ablub: db then cache.get
    assert payloads[1]["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == OP.DB_QUERY
    assert payloads[1]["name"] == "GET 'ablub'"
    assert payloads[2]["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == "cache.get"
    assert payloads[2]["name"] == "ablub"
    assert payloads[2]["attributes"][ATTRIBUTE_NAMES.CACHE_KEY] == ["ablub"]

    # ablubkeything: db then cache.get
    assert payloads[3]["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == OP.DB_QUERY
    assert payloads[3]["name"] == "GET 'ablubkeything'"
    assert payloads[4]["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == "cache.get"
    assert payloads[4]["name"] == "ablubkeything"
    assert payloads[4]["attributes"][ATTRIBUTE_NAMES.CACHE_KEY] == ["ablubkeything"]

    # abl: db only (no prefix match)
    assert payloads[5]["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == OP.DB_QUERY
    assert payloads[5]["name"] == "GET 'abl'"

    assert payloads[6]["name"] == "custom parent"


@pytest.mark.asyncio
async def test_cache_data(sentry_init, capture_items):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["myasynccache"],
            ),
        ],
        traces_sample_rate=1.0,
    )

    # Use a unique host per parametrized run so fakeredis (which shares state
    # keyed by host:port) doesn't leak the SET from a prior run into this one.
    host = f"mycacheserver-{uuid.uuid4().hex}.io"
    connection = FakeRedisAsync(host=host, port=6378)

    items = capture_items("span")
    with sentry_sdk.start_span(name="custom parent"):
        await connection.get("myasynccachekey")
        await connection.set("myasynccachekey", "事实胜于雄辩")
        await connection.get("myasynccachekey")
    sentry_sdk.flush()

    assert len(items) == 7
    payloads = [item.payload for item in items]

    # First get (miss)
    assert payloads[0]["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == OP.DB_QUERY
    cache_get_miss = payloads[1]
    assert cache_get_miss["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == "cache.get"
    assert cache_get_miss["name"] == "myasynccachekey"
    assert cache_get_miss["attributes"][ATTRIBUTE_NAMES.CACHE_KEY] == [
        "myasynccachekey"
    ]
    assert cache_get_miss["attributes"][ATTRIBUTE_NAMES.CACHE_HIT] is False
    assert ATTRIBUTE_NAMES.CACHE_ITEM_SIZE not in cache_get_miss["attributes"]
    if FAKEREDIS_VERSION <= (2, 7, 1):
        assert ATTRIBUTE_NAMES.NETWORK_PEER_PORT not in cache_get_miss["attributes"]
    else:
        assert cache_get_miss["attributes"][ATTRIBUTE_NAMES.NETWORK_PEER_PORT] == 6378
    if FAKEREDIS_VERSION <= (1, 7, 1):
        assert ATTRIBUTE_NAMES.NETWORK_PEER_ADDRESS not in cache_get_miss["attributes"]
    else:
        assert (
            cache_get_miss["attributes"][ATTRIBUTE_NAMES.NETWORK_PEER_ADDRESS] == host
        )

    # Set
    assert payloads[2]["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == OP.DB_QUERY
    cache_put = payloads[3]
    assert cache_put["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == "cache.put"
    assert cache_put["name"] == "myasynccachekey"
    assert cache_put["attributes"][ATTRIBUTE_NAMES.CACHE_KEY] == ["myasynccachekey"]
    assert ATTRIBUTE_NAMES.CACHE_HIT not in cache_put["attributes"]
    assert cache_put["attributes"][ATTRIBUTE_NAMES.CACHE_ITEM_SIZE] == 18
    if FAKEREDIS_VERSION <= (2, 7, 1):
        assert ATTRIBUTE_NAMES.NETWORK_PEER_PORT not in cache_put["attributes"]
    else:
        assert cache_put["attributes"][ATTRIBUTE_NAMES.NETWORK_PEER_PORT] == 6378
    if FAKEREDIS_VERSION <= (1, 7, 1):
        assert ATTRIBUTE_NAMES.NETWORK_PEER_ADDRESS not in cache_put["attributes"]
    else:
        assert cache_put["attributes"][ATTRIBUTE_NAMES.NETWORK_PEER_ADDRESS] == host

    # Second get (hit)
    assert payloads[4]["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == OP.DB_QUERY
    cache_get_hit = payloads[5]
    assert cache_get_hit["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == "cache.get"
    assert cache_get_hit["attributes"][ATTRIBUTE_NAMES.CACHE_KEY] == ["myasynccachekey"]
    assert cache_get_hit["attributes"][ATTRIBUTE_NAMES.CACHE_HIT] is True
    assert cache_get_hit["attributes"][ATTRIBUTE_NAMES.CACHE_ITEM_SIZE] == 18
    if FAKEREDIS_VERSION <= (2, 7, 1):
        assert ATTRIBUTE_NAMES.NETWORK_PEER_PORT not in cache_get_hit["attributes"]
    else:
        assert cache_get_hit["attributes"][ATTRIBUTE_NAMES.NETWORK_PEER_PORT] == 6378
    if FAKEREDIS_VERSION <= (1, 7, 1):
        assert ATTRIBUTE_NAMES.NETWORK_PEER_ADDRESS not in cache_get_hit["attributes"]
    else:
        assert cache_get_hit["attributes"][ATTRIBUTE_NAMES.NETWORK_PEER_ADDRESS] == host

    assert payloads[6]["name"] == "custom parent"
