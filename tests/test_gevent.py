import logging
import pickle
from datetime import datetime, timezone

import sentry_sdk
from sentry_sdk._compat import PY37, PY38

import pytest
from tests.conftest import CapturingServer

pytest.importorskip("gevent")


@pytest.fixture(scope="module")
def monkeypatched_gevent():
    try:
        import gevent

        gevent.monkey.patch_all()
    except Exception as e:
        if "_RLock__owner" in str(e):
            pytest.skip("https://github.com/gevent/gevent/issues/1380")
        else:
            raise


@pytest.fixture
def capturing_server(request):
    server = CapturingServer()
    server.start()
    request.addfinalizer(server.stop)
    return server


@pytest.fixture
def make_client(request, capturing_server):
    def inner(**kwargs):
        return sentry_sdk.Client(
            "http://foobar@{}/132".format(capturing_server.url[len("http://") :]),
            **kwargs,
        )

    return inner


@pytest.mark.forked
@pytest.mark.parametrize("debug", (True, False))
@pytest.mark.parametrize("client_flush_method", ["close", "flush"])
@pytest.mark.parametrize("use_pickle", (True, False))
@pytest.mark.parametrize("compression_level", (0, 9, None))
@pytest.mark.parametrize(
    "compression_algo",
    (("gzip", "br", "<invalid>", None) if PY37 else ("gzip", "<invalid>", None)),
)
@pytest.mark.parametrize("http2", [True, False] if PY38 else [False])
def test_transport_works_gevent(
    capturing_server,
    request,
    capsys,
    caplog,
    debug,
    make_client,
    client_flush_method,
    use_pickle,
    compression_level,
    compression_algo,
    http2,
):
    caplog.set_level(logging.DEBUG)

    experiments = {}
    if compression_level is not None:
        experiments["transport_compression_level"] = compression_level

    if compression_algo is not None:
        experiments["transport_compression_algo"] = compression_algo

    if http2:
        experiments["transport_http2"] = True

    client = make_client(
        debug=debug,
        _experiments=experiments,
    )

    if use_pickle:
        client = pickle.loads(pickle.dumps(client))

    sentry_sdk.get_global_scope().set_client(client)
    request.addfinalizer(lambda: sentry_sdk.get_global_scope().set_client(None))

    sentry_sdk.add_breadcrumb(
        level="info", message="i like bread", timestamp=datetime.now(timezone.utc)
    )
    sentry_sdk.capture_message("löl")

    getattr(client, client_flush_method)()

    out, err = capsys.readouterr()
    assert not err and not out
    assert capturing_server.captured
    should_compress = (
        # default is to compress with brotli if available, gzip otherwise
        (compression_level is None)
        or (
            # setting compression level to 0 means don't compress
            compression_level > 0
        )
    ) and (
        # if we couldn't resolve to a known algo, we don't compress
        compression_algo != "<invalid>"
    )

    assert capturing_server.captured[0].compressed == should_compress

    assert any("Sending envelope" in record.msg for record in caplog.records) == debug
