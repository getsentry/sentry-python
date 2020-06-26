import sys
import pytest

from sqlalchemy import Column, ForeignKey, Integer, String, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

from sentry_sdk import capture_message, start_transaction
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration


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

    for crumb in event["breadcrumbs"]:
        del crumb["timestamp"]

    assert event["breadcrumbs"][-2:] == [
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

    assert (
        render_span_tree(event)
        == """\
- op=None: description=None
  - op='db': description='SAVEPOINT sa_savepoint_1'
  - op='db': description='SELECT person.id AS person_id, person.name AS person_name \\nFROM person\\n LIMIT ? OFFSET ?'
  - op='db': description='RELEASE SAVEPOINT sa_savepoint_1'
  - op='db': description='SAVEPOINT sa_savepoint_2'
  - op='db': description='INSERT INTO person (id, name) VALUES (?, ?)'
  - op='db': description='ROLLBACK TO SAVEPOINT sa_savepoint_2'
  - op='db': description='SAVEPOINT sa_savepoint_3'
  - op='db': description='INSERT INTO person (id, name) VALUES (?, ?)'
  - op='db': description='ROLLBACK TO SAVEPOINT sa_savepoint_3'
  - op='db': description='SAVEPOINT sa_savepoint_4'
  - op='db': description='SELECT person.id AS person_id, person.name AS person_name \\nFROM person\\n LIMIT ? OFFSET ?'
  - op='db': description='RELEASE SAVEPOINT sa_savepoint_4'\
"""
    )
