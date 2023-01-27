import pytest

from sentry_sdk.integrations.arq import ArqIntegration

from arq.connections import ArqRedis
from arq.worker import Worker

from fakeredis.aioredis import FakeRedis


@pytest.fixture(autouse=True)
def patch_fakeredis_info_command():
    from fakeredis._fakesocket import FakeSocket

    if not hasattr(FakeSocket, "info"):
        from fakeredis._commands import command
        from fakeredis._helpers import SimpleString

        @command((SimpleString,), name="info")
        def info(self, section):
            return section

        FakeSocket.info = info


@pytest.fixture
def init_arq(sentry_init):
    def inner(functions):
        sentry_init(
            integrations=[ArqIntegration()],
            traces_sample_rate=1.0,
            send_default_pii=True,
            debug=True,
        )

        server = FakeRedis()
        pool = ArqRedis(pool_or_conn=server.connection_pool)
        return pool, Worker(functions, redis_pool=pool)

    return inner
