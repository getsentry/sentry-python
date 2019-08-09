from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from sqlalchemy import create_engine

from sentry_sdk import capture_message
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration


def test_orm_queries(sentry_init, capture_events):
    sentry_init(integrations=[SqlalchemyIntegration()])
    events = capture_events()

    Base = declarative_base()

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

    Session = sessionmaker(bind=engine)
    session = Session()

    bob = Person(name="Bob")
    session.add(bob)

    assert session.query(Person).first() == bob

    capture_message("hi")

    event, = events

    for crumb in event["breadcrumbs"]:
        del crumb["timestamp"]

    assert event["breadcrumbs"][-2:] == [
        {
            "category": "query",
            "data": {"db.params": [["Bob"]]},
            "message": "INSERT INTO person (name) VALUES (?)",
            "type": "default",
        },
        {
            "category": "query",
            "data": {"db.params": [[1, 0]]},
            "message": "SELECT person.id AS person_id, person.name AS person_name \n"
            "FROM person\n"
            " LIMIT ? OFFSET ?",
            "type": "default",
        },
    ]
