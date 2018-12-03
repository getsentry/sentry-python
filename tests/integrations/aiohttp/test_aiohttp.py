from aiohttp import web

from sentry_sdk.integrations.aiohttp import AioHttpIntegration


async def test_basic(sentry_init, aiohttp_client, loop, capture_events):
    sentry_init(integrations=[AioHttpIntegration()])

    async def hello(request):
        1 / 0

    app = web.Application()
    app.router.add_get("/", hello)

    events = capture_events()

    client = await aiohttp_client(app)
    resp = await client.get("/")
    assert resp.status == 500

    event, = events

    exception, = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"
    request = event["request"]
    host = request["headers"]["Host"]

    assert request["env"] == {"REMOTE_ADDR": "127.0.0.1"}
    assert request["method"] == "GET"
    assert request["query_string"] == ""
    assert request["url"] == f"http://{host}/"
    assert request["headers"] == {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Host": host,
        "User-Agent": "Python/3.7 aiohttp/3.4.4",
    }
