def get_request_with_client(client, url):
    client.get(url).build().send()
