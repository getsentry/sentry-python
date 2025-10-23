def get_request_with_connection(connection, url):
    connection.request("GET", url)
    connection.getresponse()
