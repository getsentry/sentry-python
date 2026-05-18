import os
from datetime import datetime
from unittest import mock

import pytest
from sqlalchemy import Column, ForeignKey, Integer, String, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

import sentry_sdk
from sentry_sdk import capture_message, start_transaction
from sentry_sdk.consts import DEFAULT_MAX_VALUE_LENGTH, SPANDATA
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from sentry_sdk.serializer import MAX_EVENT_BYTES
from sentry_sdk.tracing_utils import record_sql_queries_supporting_streaming
from sentry_sdk.utils import json_dumps


@pytest.mark.parametrize("span_streaming", [True, False])
def test_orm_queries(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[SqlalchemyIntegration()],
        _experiments={
            "record_sql_params": True,
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )

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

    if span_streaming:
        items = capture_items("event")
        capture_message("hi")
        (event,) = (item.payload for item in items if item.type == "event")
    else:
        events = capture_events()
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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_transactions(
    sentry_init,
    capture_events,
    capture_items,
    render_span_tree,
    span_streaming,
):
    sentry_init(
        integrations=[SqlalchemyIntegration()],
        _experiments={
            "record_sql_params": True,
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
        traces_sample_rate=1.0,
    )

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

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            with session.begin_nested():
                session.query(Person).first()

            for _ in range(2):
                with pytest.raises(IntegrityError):
                    with session.begin_nested():
                        session.add(Person(id=1, name="bob"))
                        session.add(Person(id=1, name="bob"))

            with session.begin_nested():
                session.query(Person).first()

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        sqlalchemy_spans = [
            span
            for span in spans
            if span["attributes"]["sentry.origin"] == "auto.db.sqlalchemy"
        ]
        for span in sqlalchemy_spans:
            assert span["attributes"][SPANDATA.DB_SYSTEM_NAME] == "sqlite"
            assert span["attributes"][SPANDATA.DB_DRIVER_NAME] == "pysqlite"
            assert span["attributes"][SPANDATA.DB_NAMESPACE] == ":memory:"
            assert SPANDATA.SERVER_PORT not in span["attributes"]

        assert (
            render_span_tree(spans)
            == """\
- sentry.op=null: name="custom parent"
  - sentry.op="db": name="SAVEPOINT sa_savepoint_1"
  - sentry.op="db": name="SELECT person.id AS person_id, person.name AS person_name \\nFROM person\\n LIMIT ? OFFSET ?"
  - sentry.op="db": name="RELEASE SAVEPOINT sa_savepoint_1"
  - sentry.op="db": name="SAVEPOINT sa_savepoint_2"
  - sentry.op="db": name="INSERT INTO person (id, name) VALUES (?, ?)"
  - sentry.op="db": name="ROLLBACK TO SAVEPOINT sa_savepoint_2"
  - sentry.op="db": name="SAVEPOINT sa_savepoint_3"
  - sentry.op="db": name="INSERT INTO person (id, name) VALUES (?, ?)"
  - sentry.op="db": name="ROLLBACK TO SAVEPOINT sa_savepoint_3"
  - sentry.op="db": name="SAVEPOINT sa_savepoint_4"
  - sentry.op="db": name="SELECT person.id AS person_id, person.name AS person_name \\nFROM person\\n LIMIT ? OFFSET ?"
  - sentry.op="db": name="RELEASE SAVEPOINT sa_savepoint_4"\
"""
        )
    else:
        events = capture_events()
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
            assert span["data"][SPANDATA.DB_DRIVER_NAME] == "pysqlite"
            assert span["data"][SPANDATA.DB_NAME] == ":memory:"
            assert SPANDATA.SERVER_ADDRESS not in span["data"]
            assert SPANDATA.SERVER_PORT not in span["data"]

        assert (
            render_span_tree(event["spans"], event["contexts"]["trace"])
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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_transactions_no_engine_url(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[SqlalchemyIntegration()],
        _experiments={
            "record_sql_params": True,
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
        traces_sample_rate=1.0,
    )

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

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            with session.begin_nested():
                session.query(Person).first()

            for _ in range(2):
                with pytest.raises(IntegrityError):
                    with session.begin_nested():
                        session.add(Person(id=1, name="bob"))
                        session.add(Person(id=1, name="bob"))

            with session.begin_nested():
                session.query(Person).first()

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        sqlalchemy_spans = [
            span
            for span in spans
            if span["attributes"]["sentry.origin"] == "auto.db.sqlalchemy"
        ]
        for span in sqlalchemy_spans:
            assert span["attributes"][SPANDATA.DB_SYSTEM_NAME] == "sqlite"
            assert span["attributes"][SPANDATA.DB_DRIVER_NAME] == "pysqlite"
            assert SPANDATA.DB_NAME not in span["attributes"]
            assert SPANDATA.SERVER_PORT not in span["attributes"]
    else:
        events = capture_events()
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
            assert span["data"][SPANDATA.DB_DRIVER_NAME] == "pysqlite"
            assert SPANDATA.DB_NAME not in span["data"]
            assert SPANDATA.SERVER_ADDRESS not in span["data"]
            assert SPANDATA.SERVER_PORT not in span["data"]


@pytest.mark.parametrize("span_streaming", [True, False])
def test_long_sql_query_preserved(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=1,
        integrations=[SqlalchemyIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            with engine.connect() as con:
                con.execute(
                    text(" UNION ".join("SELECT {}".format(i) for i in range(100)))
                )

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        name = spans[0]["name"]
        assert name.startswith("SELECT 0 UNION SELECT 1")
        assert name.endswith("SELECT 98 UNION SELECT 99")
    else:
        events = capture_events()
        with start_transaction(name="test"):
            with engine.connect() as con:
                con.execute(
                    text(" UNION ".join("SELECT {}".format(i) for i in range(100)))
                )

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
        "": {
            "len": DEFAULT_MAX_VALUE_LENGTH + 10,
            "rem": [
                ["!limit", "x", DEFAULT_MAX_VALUE_LENGTH - 3, DEFAULT_MAX_VALUE_LENGTH]
            ],
        }
    }


@pytest.mark.parametrize("span_streaming", [True, False])
def test_engine_name_not_string(
    sentry_init,
    span_streaming,
):
    sentry_init(
        integrations=[SqlalchemyIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    engine.dialect.name = b"sqlite"

    with engine.connect() as con:
        con.execute(text("SELECT 0"))


@pytest.mark.parametrize("span_streaming", [True, False])
def test_query_source_disabled(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_options = {
        "integrations": [SqlalchemyIntegration()],
        "traces_sample_rate": 1.0,
        "enable_db_query_source": False,
        "db_query_source_threshold_ms": 0,
        "_experiments": {"trace_lifecycle": "stream" if span_streaming else "static"},
    }

    sentry_init(**sentry_options)

    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
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

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        for span in spans:
            if span["attributes"].get("sentry.op") == "db" and span["name"].startswith(
                "SELECT person"
            ):
                attributes = span["attributes"]

                assert SPANDATA.CODE_LINE_NUMBER not in attributes
                assert SPANDATA.CODE_NAMESPACE not in attributes
                assert SPANDATA.CODE_FILE_PATH not in attributes
                assert SPANDATA.CODE_FUNCTION not in attributes
                break
        else:
            raise AssertionError("No db span found")

    else:
        events = capture_events()

        with start_transaction(name="test_transaction", sampled=True):
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
@pytest.mark.parametrize("span_streaming", [True, False])
def test_query_source_enabled(
    sentry_init,
    capture_events,
    capture_items,
    enable_db_query_source,
    span_streaming,
):
    sentry_options = {
        "integrations": [SqlalchemyIntegration()],
        "traces_sample_rate": 1.0,
        "db_query_source_threshold_ms": 0,
        "_experiments": {"trace_lifecycle": "stream" if span_streaming else "static"},
    }

    if enable_db_query_source is not None:
        sentry_options["enable_db_query_source"] = enable_db_query_source

    sentry_init(**sentry_options)

    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
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

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        for span in spans:
            if span["attributes"].get("sentry.op") == "db" and span["name"].startswith(
                "SELECT person"
            ):
                attributes = span["attributes"]

                assert SPANDATA.CODE_LINE_NUMBER in attributes
                assert SPANDATA.CODE_NAMESPACE in attributes
                assert SPANDATA.CODE_FILE_PATH in attributes
                assert SPANDATA.CODE_FUNCTION in attributes
                break
        else:
            raise AssertionError("No db span found")
    else:
        events = capture_events()

        with start_transaction(name="test_transaction", sampled=True):
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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_query_source(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[SqlalchemyIntegration()],
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )
    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
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

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        for span in spans:
            if span["attributes"].get("sentry.op") == "db" and span["name"].startswith(
                "SELECT person"
            ):
                attributes = span["attributes"]

                assert SPANDATA.CODE_LINE_NUMBER in attributes
                assert SPANDATA.CODE_NAMESPACE in attributes
                assert SPANDATA.CODE_FILE_PATH in attributes
                assert SPANDATA.CODE_FUNCTION in attributes

                assert type(attributes.get(SPANDATA.CODE_LINE_NUMBER)) == int
                assert attributes.get(SPANDATA.CODE_LINE_NUMBER) > 0
                assert (
                    attributes.get(SPANDATA.CODE_NAMESPACE)
                    == "tests.integrations.sqlalchemy.test_sqlalchemy"
                )
                assert attributes.get(SPANDATA.CODE_FILE_PATH).endswith(
                    "tests/integrations/sqlalchemy/test_sqlalchemy.py"
                )

                is_relative_path = attributes.get(SPANDATA.CODE_FILE_PATH)[0] != os.sep
                assert is_relative_path

                assert attributes.get(SPANDATA.CODE_FUNCTION) == "test_query_source"
                break
        else:
            raise AssertionError("No db span found")
    else:
        events = capture_events()

        with start_transaction(name="test_transaction", sampled=True):
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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_query_source_with_module_in_search_path(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    """
    Test that query source is relative to the path of the module it ran in
    """
    sentry_init(
        integrations=[SqlalchemyIntegration()],
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    from sqlalchemy_helpers.helpers import (
        add_model_to_session,
        query_first_model_from_session,
    )

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
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

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        for span in spans:
            if span["attributes"].get("sentry.op") == "db" and span["name"].startswith(
                "SELECT person"
            ):
                attributes = span["attributes"]

                assert SPANDATA.CODE_LINE_NUMBER in attributes
                assert SPANDATA.CODE_NAMESPACE in attributes
                assert SPANDATA.CODE_FILE_PATH in attributes
                assert SPANDATA.CODE_FUNCTION in attributes

                assert type(attributes.get(SPANDATA.CODE_LINE_NUMBER)) == int
                assert attributes.get(SPANDATA.CODE_LINE_NUMBER) > 0
                assert (
                    attributes.get(SPANDATA.CODE_NAMESPACE)
                    == "sqlalchemy_helpers.helpers"
                )
                assert (
                    attributes.get(SPANDATA.CODE_FILE_PATH)
                    == "sqlalchemy_helpers/helpers.py"
                )

                is_relative_path = attributes.get(SPANDATA.CODE_FILE_PATH)[0] != os.sep
                assert is_relative_path

                assert (
                    attributes.get(SPANDATA.CODE_FUNCTION)
                    == "query_first_model_from_session"
                )
                break
        else:
            raise AssertionError("No db span found")
    else:
        events = capture_events()
        with start_transaction(name="test_transaction", sampled=True):
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
                assert (
                    data.get(SPANDATA.CODE_FILEPATH) == "sqlalchemy_helpers/helpers.py"
                )

                is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
                assert is_relative_path

                assert (
                    data.get(SPANDATA.CODE_FUNCTION) == "query_first_model_from_session"
                )
                break
        else:
            raise AssertionError("No db span found")


@pytest.mark.parametrize("span_streaming", [True, False])
def test_no_query_source_if_duration_too_short(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[SqlalchemyIntegration()],
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=100,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )
    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
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
                    with record_sql_queries_supporting_streaming(
                        *args, **kwargs
                    ) as span:
                        self.span = span

                    if span_streaming:
                        self.span._start_timestamp = datetime(2024, 1, 1, microsecond=0)
                        self.span._end_timestamp = datetime(
                            2024, 1, 1, microsecond=99999
                        )
                    else:
                        self.span.start_timestamp = datetime(2024, 1, 1, microsecond=0)
                        self.span.timestamp = datetime(2024, 1, 1, microsecond=99999)

                def __enter__(self):
                    return self.span

                def __exit__(self, type, value, traceback):
                    pass

            with mock.patch(
                "sentry_sdk.integrations.sqlalchemy.record_sql_queries_supporting_streaming",
                fake_record_sql_queries,
            ):
                assert session.query(Person).first() == bob

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        for span in spans:
            if span["attributes"].get("sentry.op") == "db" and span["name"].startswith(
                "SELECT person"
            ):
                attributes = span["attributes"]

                assert SPANDATA.CODE_LINE_NUMBER not in attributes
                assert SPANDATA.CODE_NAMESPACE not in attributes
                assert SPANDATA.CODE_FILE_PATH not in attributes
                assert SPANDATA.CODE_FUNCTION not in attributes
                break
        else:
            raise AssertionError("No db span found")

    else:
        events = capture_events()

        with start_transaction(name="test_transaction", sampled=True):
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
                    with record_sql_queries_supporting_streaming(
                        *args, **kwargs
                    ) as span:
                        self.span = span

                    if span_streaming:
                        self.span._start_timestamp = datetime(2024, 1, 1, microsecond=0)
                        self.span._end_timestamp = datetime(
                            2024, 1, 1, microsecond=99999
                        )
                    else:
                        self.span.start_timestamp = datetime(2024, 1, 1, microsecond=0)
                        self.span.timestamp = datetime(2024, 1, 1, microsecond=99999)

                def __enter__(self):
                    return self.span

                def __exit__(self, type, value, traceback):
                    pass

            with mock.patch(
                "sentry_sdk.integrations.sqlalchemy.record_sql_queries_supporting_streaming",
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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_query_source_if_duration_over_threshold(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[SqlalchemyIntegration()],
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=100,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
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
                    with record_sql_queries_supporting_streaming(
                        *args, **kwargs
                    ) as span:
                        self.span = span

                    self.span._start_timestamp = datetime(2024, 1, 1, microsecond=0)
                    self.span._end_timestamp = datetime(2024, 1, 1, microsecond=101000)

                def __enter__(self):
                    return self.span

                def __exit__(self, type, value, traceback):
                    pass

            with mock.patch(
                "sentry_sdk.integrations.sqlalchemy.record_sql_queries_supporting_streaming",
                fake_record_sql_queries,
            ):
                assert session.query(Person).first() == bob

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        for span in spans:
            if span["attributes"].get("sentry.op") == "db" and span["name"].startswith(
                "SELECT person"
            ):
                attributes = span["attributes"]

                assert SPANDATA.CODE_LINE_NUMBER in attributes
                assert SPANDATA.CODE_NAMESPACE in attributes
                assert SPANDATA.CODE_FILE_PATH in attributes
                assert SPANDATA.CODE_FUNCTION in attributes

                assert type(attributes.get(SPANDATA.CODE_LINE_NUMBER)) == int
                assert attributes.get(SPANDATA.CODE_LINE_NUMBER) > 0
                assert (
                    attributes.get(SPANDATA.CODE_NAMESPACE)
                    == "tests.integrations.sqlalchemy.test_sqlalchemy"
                )
                assert attributes.get(SPANDATA.CODE_FILE_PATH).endswith(
                    "tests/integrations/sqlalchemy/test_sqlalchemy.py"
                )

                is_relative_path = attributes.get(SPANDATA.CODE_FILE_PATH)[0] != os.sep
                assert is_relative_path

                assert (
                    attributes.get(SPANDATA.CODE_FUNCTION)
                    == "test_query_source_if_duration_over_threshold"
                )
                break
        else:
            raise AssertionError("No db span found")
    else:
        events = capture_events()

        with start_transaction(name="test_transaction", sampled=True):
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
                    with record_sql_queries_supporting_streaming(
                        *args, **kwargs
                    ) as span:
                        self.span = span

                    self.span.start_timestamp = datetime(2024, 1, 1, microsecond=0)
                    self.span.timestamp = datetime(2024, 1, 1, microsecond=101000)

                def __enter__(self):
                    return self.span

                def __exit__(self, type, value, traceback):
                    pass

            with mock.patch(
                "sentry_sdk.integrations.sqlalchemy.record_sql_queries_supporting_streaming",
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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_span_origin(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[SqlalchemyIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            with engine.connect() as con:
                con.execute(text("SELECT 0"))

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]

        assert spans[0]["attributes"]["sentry.origin"] == "auto.db.sqlalchemy"
        assert spans[1]["attributes"]["sentry.origin"] == "manual"
    else:
        events = capture_events()
        with start_transaction(name="foo"):
            with engine.connect() as con:
                con.execute(text("SELECT 0"))

        (event,) = events

        assert event["contexts"]["trace"]["origin"] == "manual"
        assert event["spans"][0]["origin"] == "auto.db.sqlalchemy"
