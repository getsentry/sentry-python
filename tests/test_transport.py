# coding: utf-8
import logging
import pickle

from datetime import datetime

import eventlet
import pytest

from sentry_sdk import Hub, Client, add_breadcrumb, capture_message


@pytest.fixture(scope="session", params=[True, False])
def eventlet_maybe_patched(request):
    if request.param:
        eventlet.monkey_patch()


@pytest.fixture(params=[True, False])
def make_client(request):
    def inner(*args, **kwargs):
        client = Client(*args, **kwargs)
        if request.param:
            client = pickle.loads(pickle.dumps(client))

        return client

    return inner


@pytest.mark.parametrize("debug", (True, False))
@pytest.mark.parametrize("client_flush_method", ["close", "flush"])
def test_transport_works(
    httpserver,
    request,
    capsys,
    caplog,
    debug,
    make_client,
    client_flush_method,
    eventlet_maybe_patched,
):
    httpserver.serve_content("ok", 200)

    caplog.set_level(logging.DEBUG)

    client = make_client(
        "http://foobar@{}/123".format(httpserver.url[len("http://") :]), debug=debug
    )
    Hub.current.bind_client(client)
    request.addfinalizer(lambda: Hub.current.bind_client(None))

    add_breadcrumb(level="info", message="i like bread", timestamp=datetime.now())
    capture_message("löl")

    getattr(client, client_flush_method)()

    out, err = capsys.readouterr()
    assert not err and not out
    assert httpserver.requests

    assert any("Sending event" in record.msg for record in caplog.records) == debug
