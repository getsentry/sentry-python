async def execute_query_in_connection(query, connection):
    await connection.execute(query)
