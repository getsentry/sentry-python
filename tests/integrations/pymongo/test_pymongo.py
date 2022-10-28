from sentry_sdk import capture_message, start_transaction
from sentry_sdk.integrations.pymongo import PyMongoIntegration

from mockupdb import MockupDB, OpQuery
from pymongo import MongoClient
import pytest


@pytest.fixture(scope="session")
def mongo_server():
    server = MockupDB(verbose=True)
    server.autoresponds("ismaster", maxWireVersion=6)
    server.run()
    server.autoresponds(
        {"find": "test_collection"}, cursor={"id": 123, "firstBatch": []}
    )
    # Find query changed somewhere between PyMongo 3.1 and 3.12.
    # This line is to respond to "find" queries sent by old PyMongo the same way it's done above.
    server.autoresponds(OpQuery({"foobar": 1}), cursor={"id": 123, "firstBatch": []})
    server.autoresponds({"insert": "test_collection"}, ok=1)
    server.autoresponds({"insert": "erroneous"}, ok=0, errmsg="test error")
    yield server
    server.stop()


@pytest.mark.parametrize("with_pii", [False, True])
def test_transactions(sentry_init, capture_events, mongo_server, with_pii):
    sentry_init(
        integrations=[PyMongoIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=with_pii,
    )
    events = capture_events()

    connection = MongoClient(mongo_server.uri)

    with start_transaction():
        list(
            connection["test_db"]["test_collection"].find({"foobar": 1})
        )  # force query execution
        connection["test_db"]["test_collection"].insert_one({"foo": 2})
        try:
            connection["test_db"]["erroneous"].insert_many([{"bar": 3}, {"baz": 3}])
            pytest.fail("Request should raise")
        except Exception:
            pass

    (event,) = events
    (find, insert_success, insert_fail) = event["spans"]

    common_tags = {
        "db.name": "test_db",
        "db.system": "mongodb",
        "net.peer.name": mongo_server.host,
        "net.peer.port": str(mongo_server.port),
    }
    for span in find, insert_success, insert_fail:
        for field, value in common_tags.items():
            assert span["tags"][field] == value

    assert find["op"] == "db.query"
    assert insert_success["op"] == "db.query"
    assert insert_fail["op"] == "db.query"

    assert find["tags"]["db.operation"] == "find"
    assert insert_success["tags"]["db.operation"] == "insert"
    assert insert_fail["tags"]["db.operation"] == "insert"

    assert find["description"].startswith("find {")
    assert insert_success["description"].startswith("insert {")
    assert insert_fail["description"].startswith("insert {")
    if with_pii:
        assert "'foobar'" in find["description"]
        assert "'foo'" in insert_success["description"]
        assert (
            "'bar'" in insert_fail["description"]
            and "'baz'" in insert_fail["description"]
        )
    else:
        # All keys below top level replaced by "%s"
        assert "'foobar'" not in find["description"]
        assert "'foo'" not in insert_success["description"]
        assert (
            "'bar'" not in insert_fail["description"]
            and "'baz'" not in insert_fail["description"]
        )

    assert find["tags"]["status"] == "ok"
    assert insert_success["tags"]["status"] == "ok"
    assert insert_fail["tags"]["status"] == "internal_error"


@pytest.mark.parametrize("with_pii", [False, True])
def test_breadcrumbs(sentry_init, capture_events, mongo_server, with_pii):
    sentry_init(
        integrations=[PyMongoIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=with_pii,
    )
    events = capture_events()

    connection = MongoClient(mongo_server.uri)

    list(
        connection["test_db"]["test_collection"].find({"foobar": 1})
    )  # force query execution
    capture_message("hi")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]

    assert crumb["category"] == "query"
    assert crumb["message"].startswith("find {")
    if with_pii:
        assert "'foobar'" in crumb["message"]
    else:
        assert "'foobar'" not in crumb["message"]
    assert crumb["type"] == "db.query"
    assert crumb["data"] == {
        "db.name": "test_db",
        "db.system": "mongodb",
        "db.operation": "find",
        "net.peer.name": mongo_server.host,
        "net.peer.port": str(mongo_server.port),
    }
