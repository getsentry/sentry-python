async def get_request_with_client(client, url):
    await client.get(url)
