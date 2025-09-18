from sentry_sdk import capture_message, start_transaction
from sentry_sdk.consts import ATTRS
from sentry_sdk.integrations.pymongo import PyMongoIntegration, _strip_pii

from mockupdb import MockupDB, OpQuery
from pymongo import MongoClient
import pytest


@pytest.fixture(scope="session")
def mongo_server():
    server = MockupDB(verbose=True)
    server.autoresponds("ismaster", maxWireVersion=8)
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
            connection["test_db"]["erroneous"].insert_many([{"bar": 3}, {"baz": 4}])
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
        assert span["data"][ATTRS.DB_SYSTEM] == "mongodb"
        assert span["data"][ATTRS.DB_NAME] == "test_db"
        assert span["data"][ATTRS.SERVER_ADDRESS] == "localhost"
        assert span["data"][ATTRS.SERVER_PORT] == mongo_server.port
        for field, value in common_tags.items():
            assert span["tags"][field] == value
            assert span["data"][field] == value

    assert find["op"] == "db"
    assert insert_success["op"] == "db"
    assert insert_fail["op"] == "db"

    assert find["data"]["db.operation"] == "find"
    assert find["tags"]["db.operation"] == "find"
    assert insert_success["data"]["db.operation"] == "insert"
    assert insert_success["tags"]["db.operation"] == "insert"
    assert insert_fail["data"]["db.operation"] == "insert"
    assert insert_fail["tags"]["db.operation"] == "insert"

    assert find["description"].startswith('{"find')
    assert insert_success["description"].startswith('{"insert')
    assert insert_fail["description"].startswith('{"insert')

    assert find["data"][ATTRS.DB_MONGODB_COLLECTION] == "test_collection"
    assert find["tags"][ATTRS.DB_MONGODB_COLLECTION] == "test_collection"
    assert insert_success["data"][ATTRS.DB_MONGODB_COLLECTION] == "test_collection"
    assert insert_success["tags"][ATTRS.DB_MONGODB_COLLECTION] == "test_collection"
    assert insert_fail["data"][ATTRS.DB_MONGODB_COLLECTION] == "erroneous"
    assert insert_fail["tags"][ATTRS.DB_MONGODB_COLLECTION] == "erroneous"
    if with_pii:
        assert "1" in find["description"]
        assert "2" in insert_success["description"]
        assert "3" in insert_fail["description"] and "4" in insert_fail["description"]
    else:
        # All values in filter replaced by "%s"
        assert "1" not in find["description"]
        # All keys below top level replaced by "%s"
        assert "2" not in insert_success["description"]
        assert (
            "3" not in insert_fail["description"]
            and "4" not in insert_fail["description"]
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
    assert crumb["message"].startswith('{"find')
    if with_pii:
        assert "1" in crumb["message"]
    else:
        assert "1" not in crumb["message"]
    assert crumb["type"] == "db"
    assert crumb["data"] == {
        "db.name": "test_db",
        "db.system": "mongodb",
        "db.operation": "find",
        "net.peer.name": mongo_server.host,
        "net.peer.port": str(mongo_server.port),
        "db.mongodb.collection": "test_collection",
    }


@pytest.mark.parametrize(
    "testcase",
    [
        {
            "command": {
                "insert": "my_collection",
                "ordered": True,
                "documents": [
                    {
                        "username": "anton2",
                        "email": "anton@somewhere.io",
                        "password": "c4e86722fb56d946f7ddeecdae47e1c4458bf98a0a3ee5d5113111adf7bf0175",
                        "_id": "635bc7403cb4f8a736f61cf2",
                    }
                ],
            },
            "command_stripped": {
                "insert": "my_collection",
                "ordered": True,
                "documents": [
                    {"username": "%s", "email": "%s", "password": "%s", "_id": "%s"}
                ],
            },
        },
        {
            "command": {
                "insert": "my_collection",
                "ordered": True,
                "documents": [
                    {
                        "username": "indiana4",
                        "email": "indy@jones.org",
                        "password": "63e86722fb56d946f7ddeecdae47e1c4458bf98a0a3ee5d5113111adf7bf016b",
                        "_id": "635bc7403cb4f8a736f61cf3",
                    }
                ],
            },
            "command_stripped": {
                "insert": "my_collection",
                "ordered": True,
                "documents": [
                    {"username": "%s", "email": "%s", "password": "%s", "_id": "%s"}
                ],
            },
        },
        {
            "command": {
                "find": "my_collection",
                "filter": {},
                "limit": 1,
                "singleBatch": True,
            },
            "command_stripped": {
                "find": "my_collection",
                "filter": {},
                "limit": 1,
                "singleBatch": True,
            },
        },
        {
            "command": {
                "find": "my_collection",
                "filter": {"username": "notthere"},
                "limit": 1,
                "singleBatch": True,
            },
            "command_stripped": {
                "find": "my_collection",
                "filter": {"username": "%s"},
                "limit": 1,
                "singleBatch": True,
            },
        },
        {
            "command": {
                "insert": "my_collection",
                "ordered": True,
                "documents": [
                    {
                        "username": "userx1",
                        "email": "x@somewhere.io",
                        "password": "ccc86722fb56d946f7ddeecdae47e1c4458bf98a0a3ee5d5113111adf7bf0175",
                        "_id": "635bc7403cb4f8a736f61cf4",
                    },
                    {
                        "username": "userx2",
                        "email": "x@somewhere.io",
                        "password": "xxx86722fb56d946f7ddeecdae47e1c4458bf98a0a3ee5d5113111adf7bf0175",
                        "_id": "635bc7403cb4f8a736f61cf5",
                    },
                ],
            },
            "command_stripped": {
                "insert": "my_collection",
                "ordered": True,
                "documents": [
                    {"username": "%s", "email": "%s", "password": "%s", "_id": "%s"},
                    {"username": "%s", "email": "%s", "password": "%s", "_id": "%s"},
                ],
            },
        },
        {
            "command": {
                "find": "my_collection",
                "filter": {"email": "ada@lovelace.com"},
            },
            "command_stripped": {"find": "my_collection", "filter": {"email": "%s"}},
        },
        {
            "command": {
                "aggregate": "my_collection",
                "pipeline": [{"$match": {}}, {"$group": {"_id": 1, "n": {"$sum": 1}}}],
                "cursor": {},
            },
            "command_stripped": {
                "aggregate": "my_collection",
                "pipeline": [{"$match": {}}, {"$group": {"_id": 1, "n": {"$sum": 1}}}],
                "cursor": "%s",
            },
        },
        {
            "command": {
                "aggregate": "my_collection",
                "pipeline": [
                    {"$match": {"email": "x@somewhere.io"}},
                    {"$group": {"_id": 1, "n": {"$sum": 1}}},
                ],
                "cursor": {},
            },
            "command_stripped": {
                "aggregate": "my_collection",
                "pipeline": [
                    {"$match": {"email": "%s"}},
                    {"$group": {"_id": 1, "n": {"$sum": 1}}},
                ],
                "cursor": "%s",
            },
        },
        {
            "command": {
                "createIndexes": "my_collection",
                "indexes": [{"name": "username_1", "key": [("username", 1)]}],
            },
            "command_stripped": {
                "createIndexes": "my_collection",
                "indexes": [{"name": "username_1", "key": [("username", 1)]}],
            },
        },
        {
            "command": {
                "update": "my_collection",
                "ordered": True,
                "updates": [
                    ("q", {"email": "anton@somewhere.io"}),
                    (
                        "u",
                        {
                            "email": "anton2@somwehre.io",
                            "extra_field": "extra_content",
                            "new": "bla",
                        },
                    ),
                    ("multi", False),
                    ("upsert", False),
                ],
            },
            "command_stripped": {
                "update": "my_collection",
                "ordered": True,
                "updates": "%s",
            },
        },
        {
            "command": {
                "update": "my_collection",
                "ordered": True,
                "updates": [
                    ("q", {"email": "anton2@somwehre.io"}),
                    ("u", {"$rename": {"new": "new_field"}}),
                    ("multi", False),
                    ("upsert", False),
                ],
            },
            "command_stripped": {
                "update": "my_collection",
                "ordered": True,
                "updates": "%s",
            },
        },
        {
            "command": {
                "update": "my_collection",
                "ordered": True,
                "updates": [
                    ("q", {"email": "x@somewhere.io"}),
                    ("u", {"$rename": {"password": "pwd"}}),
                    ("multi", True),
                    ("upsert", False),
                ],
            },
            "command_stripped": {
                "update": "my_collection",
                "ordered": True,
                "updates": "%s",
            },
        },
        {
            "command": {
                "delete": "my_collection",
                "ordered": True,
                "deletes": [("q", {"username": "userx2"}), ("limit", 1)],
            },
            "command_stripped": {
                "delete": "my_collection",
                "ordered": True,
                "deletes": "%s",
            },
        },
        {
            "command": {
                "delete": "my_collection",
                "ordered": True,
                "deletes": [("q", {"email": "xplus@somewhere.io"}), ("limit", 0)],
            },
            "command_stripped": {
                "delete": "my_collection",
                "ordered": True,
                "deletes": "%s",
            },
        },
        {
            "command": {
                "findAndModify": "my_collection",
                "query": {"email": "ada@lovelace.com"},
                "new": False,
                "remove": True,
            },
            "command_stripped": {
                "findAndModify": "my_collection",
                "query": {"email": "%s"},
                "new": "%s",
                "remove": "%s",
            },
        },
        {
            "command": {
                "findAndModify": "my_collection",
                "query": {"email": "anton2@somewhere.io"},
                "new": False,
                "update": {"email": "anton3@somwehre.io", "extra_field": "xxx"},
                "upsert": False,
            },
            "command_stripped": {
                "findAndModify": "my_collection",
                "query": {"email": "%s"},
                "new": "%s",
                "update": {"email": "%s", "extra_field": "%s"},
                "upsert": "%s",
            },
        },
        {
            "command": {
                "findAndModify": "my_collection",
                "query": {"email": "anton3@somewhere.io"},
                "new": False,
                "update": {"$rename": {"extra_field": "extra_field2"}},
                "upsert": False,
            },
            "command_stripped": {
                "findAndModify": "my_collection",
                "query": {"email": "%s"},
                "new": "%s",
                "update": {"$rename": "%s"},
                "upsert": "%s",
            },
        },
        {
            "command": {
                "renameCollection": "test.my_collection",
                "to": "test.new_collection",
            },
            "command_stripped": {
                "renameCollection": "test.my_collection",
                "to": "test.new_collection",
            },
        },
        {
            "command": {"drop": "new_collection"},
            "command_stripped": {"drop": "new_collection"},
        },
    ],
)
def test_strip_pii(testcase):
    assert _strip_pii(testcase["command"]) == testcase["command_stripped"]


def test_span_origin(sentry_init, capture_events, mongo_server):
    sentry_init(
        integrations=[PyMongoIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    connection = MongoClient(mongo_server.uri)

    with start_transaction():
        list(
            connection["test_db"]["test_collection"].find({"foobar": 1})
        )  # force query execution

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.db.pymongo"
