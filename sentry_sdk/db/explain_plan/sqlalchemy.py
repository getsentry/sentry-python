from sentry_sdk.db.explain_plan import cache_statement, should_run_explain_plan
from sentry_sdk.integrations import DidNotEnable

try:
    from sqlalchemy.sql import text  # type: ignore
except ImportError:
    raise DidNotEnable("SQLAlchemy not installed.")


def attach_explain_plan_to_span(span, connection, statement, parameters, options):
    """
    Run EXPLAIN or EXPLAIN ANALYZE on the given statement and attach the explain plan to the span data.
    """
    if not statement.strip().upper().startswith("SELECT"):
        return

    if not should_run_explain_plan(statement):
        return

    explain_statement = ("EXPLAIN ANALYZE " + statement) % parameters

    result = connection.execute(text(explain_statement))
    explain_plan = [row for row in result]

    span.set_data("db.explain_plan", explain_plan)
    cache_statement(statement)
