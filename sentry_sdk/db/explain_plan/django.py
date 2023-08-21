from sentry_sdk.db.explain_plan import cache_statement, should_run_explain_plan


def attach_explain_plan_to_span(
    span, connection, statement, parameters, mogrify, options
):
    """
    Run EXPLAIN or EXPLAIN ANALYZE on the given statement and attach the explain plan to the span data.
    """
    if not statement.strip().upper().startswith("SELECT"):
        return

    if not should_run_explain_plan(statement, options):
        return

    explain_statement = "EXPLAIN ANALYZE " + mogrify(statement, parameters).decode(
        "utf-8"
    )

    with connection.cursor() as cursor:
        cursor.execute(explain_statement)
        explain_plan = [row for row in cursor.fetchall()]

        span.set_data("db.explain_plan", explain_plan)
        cache_statement(statement)
