import sys
import pytest

from sqlalchemy import Column, ForeignKey, Integer, String, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from sqlalchemy import text

from sentry_sdk import capture_message, start_transaction, configure_scope
from sentry_sdk.consts import DEFAULT_MAX_VALUE_LENGTH, SPANDATA
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from sentry_sdk.serializer import MAX_EVENT_BYTES
from sentry_sdk.utils import json_dumps


def test_orm_queries(sentry_init, capture_events):
    sentry_init(
        integrations=[SqlalchemyIntegration()], _experiments={"record_sql_params": True}
    )
    events = capture_events()

    Base = declarative_base()  # noqa: N806

    class Person(Base):
        __tablename__ = "person"
        id = Column(Integer, primary_key=True)
        name = Column(String(250), nullable=False)

    class Address(Base):
        __tablename__ = "address"
        id = Column(Integer, primary_key=True)
        street_name = Column(String(250))
        street_number = Column(String(250))
        post_code = Column(String(250), nullable=False)
        person_id = Column(Integer, ForeignKey("person.id"))
        person = relationship(Person)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine)  # noqa: N806
    session = Session()

    bob = Person(name="Bob")
    session.add(bob)

    assert session.query(Person).first() == bob

    capture_message("hi")

    (event,) = events

    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    assert event["breadcrumbs"]["values"][-2:] == [
        {
            "category": "query",
            "data": {"db.params": ["Bob"], "db.paramstyle": "qmark"},
            "message": "INSERT INTO person (name) VALUES (?)",
            "type": "default",
        },
        {
            "category": "query",
            "data": {"db.params": [1, 0], "db.paramstyle": "qmark"},
            "message": "SELECT person.id AS person_id, person.name AS person_name \n"
            "FROM person\n"
            " LIMIT ? OFFSET ?",
            "type": "default",
        },
    ]


@pytest.mark.skipif(
    sys.version_info < (3,), reason="This sqla usage seems to be broken on Py2"
)
def test_transactions(sentry_init, capture_events, render_span_tree):
    sentry_init(
        integrations=[SqlalchemyIntegration()],
        _experiments={"record_sql_params": True},
        traces_sample_rate=1.0,
    )
    events = capture_events()

    Base = declarative_base()  # noqa: N806

    class Person(Base):
        __tablename__ = "person"
        id = Column(Integer, primary_key=True)
        name = Column(String(250), nullable=False)

    class Address(Base):
        __tablename__ = "address"
        id = Column(Integer, primary_key=True)
        street_name = Column(String(250))
        street_number = Column(String(250))
        post_code = Column(String(250), nullable=False)
        person_id = Column(Integer, ForeignKey("person.id"))
        person = relationship(Person)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine)  # noqa: N806
    session = Session()

    with start_transaction(name="test_transaction", sampled=True):
        with session.begin_nested():
            session.query(Person).first()

        for _ in range(2):
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(Person(id=1, name="bob"))
                    session.add(Person(id=1, name="bob"))

        with session.begin_nested():
            session.query(Person).first()

    (event,) = events

    for span in event["spans"]:
        assert span["data"][SPANDATA.DB_SYSTEM] == "sqlite"
        assert span["data"][SPANDATA.DB_NAME] == ":memory:"
        assert SPANDATA.SERVER_ADDRESS not in span["data"]
        assert SPANDATA.SERVER_PORT not in span["data"]

    assert (
        render_span_tree(event)
        == """\
- op=null: description=null
  - op="db": description="SAVEPOINT sa_savepoint_1"
  - op="db": description="SELECT person.id AS person_id, person.name AS person_name \\nFROM person\\n LIMIT ? OFFSET ?"
  - op="db": description="RELEASE SAVEPOINT sa_savepoint_1"
  - op="db": description="SAVEPOINT sa_savepoint_2"
  - op="db": description="INSERT INTO person (id, name) VALUES (?, ?)"
  - op="db": description="ROLLBACK TO SAVEPOINT sa_savepoint_2"
  - op="db": description="SAVEPOINT sa_savepoint_3"
  - op="db": description="INSERT INTO person (id, name) VALUES (?, ?)"
  - op="db": description="ROLLBACK TO SAVEPOINT sa_savepoint_3"
  - op="db": description="SAVEPOINT sa_savepoint_4"
  - op="db": description="SELECT person.id AS person_id, person.name AS person_name \\nFROM person\\n LIMIT ? OFFSET ?"
  - op="db": description="RELEASE SAVEPOINT sa_savepoint_4"\
"""
    )


def test_long_sql_query_preserved(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1,
        integrations=[SqlalchemyIntegration()],
    )
    events = capture_events()

    engine = create_engine("sqlite:///:memory:")
    with start_transaction(name="test"):
        with engine.connect() as con:
            con.execute(text(" UNION ".join("SELECT {}".format(i) for i in range(100))))

    (event,) = events
    description = event["spans"][0]["description"]
    assert description.startswith("SELECT 0 UNION SELECT 1")
    assert description.endswith("SELECT 98 UNION SELECT 99")


def test_large_event_not_truncated(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1,
        integrations=[SqlalchemyIntegration()],
    )
    events = capture_events()

    long_str = "x" * (DEFAULT_MAX_VALUE_LENGTH + 10)

    with configure_scope() as scope:

        @scope.add_event_processor
        def processor(event, hint):
            event["message"] = long_str
            return event

    engine = create_engine("sqlite:///:memory:")
    with start_transaction(name="test"):
        with engine.connect() as con:
            for _ in range(1500):
                con.execute(
                    text(" UNION ".join("SELECT {}".format(i) for i in range(100)))
                )

    (event,) = events

    assert len(json_dumps(event)) > MAX_EVENT_BYTES

    # Some spans are discarded.
    assert len(event["spans"]) == 1000

    # Span descriptions are not truncated.
    description = event["spans"][0]["description"]
    assert len(description) == 1583
    assert description.startswith("SELECT 0")
    assert description.endswith("SELECT 98 UNION SELECT 99")

    description = event["spans"][999]["description"]
    assert len(description) == 1583
    assert description.startswith("SELECT 0")
    assert description.endswith("SELECT 98 UNION SELECT 99")

    # Smoke check that truncation of other fields has not changed.
    assert len(event["message"]) == DEFAULT_MAX_VALUE_LENGTH

    # The _meta for other truncated fields should be there as well.
    assert event["_meta"]["message"] == {
        "": {"len": 1034, "rem": [["!limit", "x", 1021, 1024]]}
    }


def test_engine_name_not_string(sentry_init):
    sentry_init(
        integrations=[SqlalchemyIntegration()],
    )

    engine = create_engine("sqlite:///:memory:")
    engine.dialect.name = b"sqlite"

    with engine.connect() as con:
        con.execute(text("SELECT 0"))
