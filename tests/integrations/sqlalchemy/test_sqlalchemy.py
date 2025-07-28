import contextlib
import os
from datetime import datetime
from unittest import mock

import pytest
from freezegun import freeze_time
from sqlalchemy import Column, ForeignKey, Integer, String, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from sqlalchemy import text

import sentry_sdk
from sentry_sdk.consts import DEFAULT_MAX_VALUE_LENGTH, SPANDATA
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from sentry_sdk.tracing_utils import record_sql_queries


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

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine)  # noqa: N806
    session = Session()

    bob = Person(name="Bob")
    session.add(bob)

    assert session.query(Person).first() == bob

    sentry_sdk.capture_message("hi")

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

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine)  # noqa: N806
    session = Session()

    with sentry_sdk.start_span(name="test_transaction", sampled=True):
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
    assert event["transaction"] == "test_transaction"

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


def test_transactions_no_engine_url(sentry_init, capture_events):
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

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    engine.url = None
    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine)  # noqa: N806
    session = Session()

    with sentry_sdk.start_span(name="test_transaction", sampled=True):
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
        assert SPANDATA.DB_NAME not in span["data"]
        assert SPANDATA.SERVER_ADDRESS not in span["data"]
        assert SPANDATA.SERVER_PORT not in span["data"]


def test_long_sql_query_preserved(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1,
        integrations=[SqlalchemyIntegration()],
    )
    events = capture_events()

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    with sentry_sdk.start_span(name="test"):
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

    scope = sentry_sdk.get_isolation_scope()

    @scope.add_event_processor
    def processor(event, hint):
        event["message"] = long_str
        return event

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    with sentry_sdk.start_span(name="test"):
        with engine.connect() as con:
            for _ in range(1500):
                con.execute(
                    text(" UNION ".join("SELECT {}".format(i) for i in range(100)))
                )

    (event,) = events

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

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    engine.dialect.name = b"sqlite"

    with engine.connect() as con:
        con.execute(text("SELECT 0"))


def test_query_source_disabled(sentry_init, capture_events):
    sentry_init(
        integrations=[SqlalchemyIntegration()],
        traces_sample_rate=1.0,
        enable_db_query_source=False,
        db_query_source_threshold_ms=0,
    )

    events = capture_events()

    with sentry_sdk.start_span(name="test_transaction", sampled=True):
        Base = declarative_base()  # noqa: N806

        class Person(Base):
            __tablename__ = "person"
            id = Column(Integer, primary_key=True)
            name = Column(String(250), nullable=False)

        engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(engine)

        Session = sessionmaker(bind=engine)  # noqa: N806
        session = Session()

        bob = Person(name="Bob")
        session.add(bob)

        assert session.query(Person).first() == bob

    (event,) = events

    for span in event["spans"]:
        if span.get("op") == "db" and span.get("description").startswith(
            "SELECT person"
        ):
            data = span.get("data", {})

            assert SPANDATA.CODE_LINENO not in data
            assert SPANDATA.CODE_NAMESPACE not in data
            assert SPANDATA.CODE_FILEPATH not in data
            assert SPANDATA.CODE_FUNCTION not in data
            break
    else:
        raise AssertionError("No db span found")


@pytest.mark.parametrize("enable_db_query_source", [None, True])
def test_query_source_enabled(sentry_init, capture_events, enable_db_query_source):
    sentry_options = {
        "integrations": [SqlalchemyIntegration()],
        "traces_sample_rate": 1.0,
        "db_query_source_threshold_ms": 0,
    }
    if enable_db_query_source is not None:
        sentry_options["enable_db_query_source"] = enable_db_query_source

    sentry_init(**sentry_options)

    events = capture_events()

    with sentry_sdk.start_span(name="test_transaction", sampled=True):
        Base = declarative_base()  # noqa: N806

        class Person(Base):
            __tablename__ = "person"
            id = Column(Integer, primary_key=True)
            name = Column(String(250), nullable=False)

        engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(engine)

        Session = sessionmaker(bind=engine)  # noqa: N806
        session = Session()

        bob = Person(name="Bob")
        session.add(bob)

        assert session.query(Person).first() == bob

    (event,) = events

    for span in event["spans"]:
        if span.get("op") == "db" and span.get("description").startswith(
            "SELECT person"
        ):
            data = span.get("data", {})

            assert SPANDATA.CODE_LINENO in data
            assert SPANDATA.CODE_NAMESPACE in data
            assert SPANDATA.CODE_FILEPATH in data
            assert SPANDATA.CODE_FUNCTION in data
            break
    else:
        raise AssertionError("No db span found")


def test_query_source(sentry_init, capture_events):
    sentry_init(
        integrations=[SqlalchemyIntegration()],
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=0,
    )
    events = capture_events()

    with sentry_sdk.start_span(name="test_transaction", sampled=True):
        Base = declarative_base()  # noqa: N806

        class Person(Base):
            __tablename__ = "person"
            id = Column(Integer, primary_key=True)
            name = Column(String(250), nullable=False)

        engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(engine)

        Session = sessionmaker(bind=engine)  # noqa: N806
        session = Session()

        bob = Person(name="Bob")
        session.add(bob)

        assert session.query(Person).first() == bob

    (event,) = events

    for span in event["spans"]:
        if span.get("op") == "db" and span.get("description").startswith(
            "SELECT person"
        ):
            data = span.get("data", {})

            assert SPANDATA.CODE_LINENO in data
            assert SPANDATA.CODE_NAMESPACE in data
            assert SPANDATA.CODE_FILEPATH in data
            assert SPANDATA.CODE_FUNCTION in data

            assert type(data.get(SPANDATA.CODE_LINENO)) == int
            assert data.get(SPANDATA.CODE_LINENO) > 0
            assert (
                data.get(SPANDATA.CODE_NAMESPACE)
                == "tests.integrations.sqlalchemy.test_sqlalchemy"
            )
            assert data.get(SPANDATA.CODE_FILEPATH).endswith(
                "tests/integrations/sqlalchemy/test_sqlalchemy.py"
            )

            is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
            assert is_relative_path

            assert data.get(SPANDATA.CODE_FUNCTION) == "test_query_source"
            break
    else:
        raise AssertionError("No db span found")


def test_query_source_with_module_in_search_path(sentry_init, capture_events):
    """
    Test that query source is relative to the path of the module it ran in
    """
    sentry_init(
        integrations=[SqlalchemyIntegration()],
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=0,
    )
    events = capture_events()

    from sqlalchemy_helpers.helpers import (
        add_model_to_session,
        query_first_model_from_session,
    )

    with sentry_sdk.start_span(name="test_transaction", sampled=True):
        Base = declarative_base()  # noqa: N806

        class Person(Base):
            __tablename__ = "person"
            id = Column(Integer, primary_key=True)
            name = Column(String(250), nullable=False)

        engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(engine)

        Session = sessionmaker(bind=engine)  # noqa: N806
        session = Session()

        bob = Person(name="Bob")

        add_model_to_session(bob, session)

        assert query_first_model_from_session(Person, session) == bob

    (event,) = events

    for span in event["spans"]:
        if span.get("op") == "db" and span.get("description").startswith(
            "SELECT person"
        ):
            data = span.get("data", {})

            assert SPANDATA.CODE_LINENO in data
            assert SPANDATA.CODE_NAMESPACE in data
            assert SPANDATA.CODE_FILEPATH in data
            assert SPANDATA.CODE_FUNCTION in data

            assert type(data.get(SPANDATA.CODE_LINENO)) == int
            assert data.get(SPANDATA.CODE_LINENO) > 0
            assert data.get(SPANDATA.CODE_NAMESPACE) == "sqlalchemy_helpers.helpers"
            assert data.get(SPANDATA.CODE_FILEPATH) == "sqlalchemy_helpers/helpers.py"

            is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
            assert is_relative_path

            assert data.get(SPANDATA.CODE_FUNCTION) == "query_first_model_from_session"
            break
    else:
        raise AssertionError("No db span found")


def test_no_query_source_if_duration_too_short(sentry_init, capture_events):
    sentry_init(
        integrations=[SqlalchemyIntegration()],
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=100,
    )
    events = capture_events()

    with sentry_sdk.start_span(name="test_transaction", sampled=True):
        Base = declarative_base()  # noqa: N806

        class Person(Base):
            __tablename__ = "person"
            id = Column(Integer, primary_key=True)
            name = Column(String(250), nullable=False)

        engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(engine)

        Session = sessionmaker(bind=engine)  # noqa: N806
        session = Session()

        bob = Person(name="Bob")
        session.add(bob)

        class fake_record_sql_queries:  # noqa: N801
            def __init__(self, *args, **kwargs):
                with freeze_time(datetime(2024, 1, 1, microsecond=0)):
                    with record_sql_queries(*args, **kwargs) as span:
                        self.span = span
                        freezer = freeze_time(datetime(2024, 1, 1, microsecond=99999))
                        freezer.start()

                    freezer.stop()

            def __enter__(self):
                return self.span

            def __exit__(self, type, value, traceback):
                pass

        with mock.patch(
            "sentry_sdk.integrations.sqlalchemy.record_sql_queries",
            fake_record_sql_queries,
        ):
            assert session.query(Person).first() == bob

    (event,) = events

    for span in event["spans"]:
        if span.get("op") == "db" and span.get("description").startswith(
            "SELECT person"
        ):
            data = span.get("data", {})

            assert SPANDATA.CODE_LINENO not in data
            assert SPANDATA.CODE_NAMESPACE not in data
            assert SPANDATA.CODE_FILEPATH not in data
            assert SPANDATA.CODE_FUNCTION not in data

            break
    else:
        raise AssertionError("No db span found")


def test_query_source_if_duration_over_threshold(sentry_init, capture_events):
    sentry_init(
        integrations=[SqlalchemyIntegration()],
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=100,
    )
    events = capture_events()

    with sentry_sdk.start_span(name="test_transaction", sampled=True):
        Base = declarative_base()  # noqa: N806

        class Person(Base):
            __tablename__ = "person"
            id = Column(Integer, primary_key=True)
            name = Column(String(250), nullable=False)

        engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(engine)

        Session = sessionmaker(bind=engine)  # noqa: N806
        session = Session()

        bob = Person(name="Bob")
        session.add(bob)

        @contextlib.contextmanager
        def fake_record_sql_queries(*args, **kwargs):  # noqa: N801
            with freeze_time(datetime(2024, 1, 1, second=0)):
                with record_sql_queries(*args, **kwargs) as span:
                    freezer = freeze_time(datetime(2024, 1, 1, second=1))
                    freezer.start()
                    yield span

                freezer.stop()

        with mock.patch(
            "sentry_sdk.integrations.sqlalchemy.record_sql_queries",
            fake_record_sql_queries,
        ):
            assert session.query(Person).first() == bob

    (event,) = events

    for span in event["spans"]:
        if span.get("op") == "db" and span.get("description").startswith(
            "SELECT person"
        ):
            data = span.get("data", {})

            assert SPANDATA.CODE_LINENO in data
            assert SPANDATA.CODE_NAMESPACE in data
            assert SPANDATA.CODE_FILEPATH in data
            assert SPANDATA.CODE_FUNCTION in data

            assert type(data.get(SPANDATA.CODE_LINENO)) == int
            assert data.get(SPANDATA.CODE_LINENO) > 0
            assert (
                data.get(SPANDATA.CODE_NAMESPACE)
                == "tests.integrations.sqlalchemy.test_sqlalchemy"
            )
            assert data.get(SPANDATA.CODE_FILEPATH).endswith(
                "tests/integrations/sqlalchemy/test_sqlalchemy.py"
            )

            is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
            assert is_relative_path

            assert (
                data.get(SPANDATA.CODE_FUNCTION)
                == "test_query_source_if_duration_over_threshold"
            )
            break
    else:
        raise AssertionError("No db span found")


def test_span_origin(sentry_init, capture_events):
    sentry_init(
        integrations=[SqlalchemyIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    with sentry_sdk.start_span(name="foo"):
        with engine.connect() as con:
            con.execute(text("SELECT 0"))

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.db.sqlalchemy"
