def get_request_with_client(client, url):
    client.get(url)


async def async_get_request_with_client(client, url):
    await client.get(url)
