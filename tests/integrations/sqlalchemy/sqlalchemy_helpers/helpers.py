def add_model_to_session(model, session):
    session.add(model)
    session.commit()


def query_first_model_from_session(model_klass, session):
    return session.query(model_klass).first()
