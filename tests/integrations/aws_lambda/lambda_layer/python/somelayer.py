import urllib.request
import urllib.parse
import json


def layer_function():
    data = json.dumps({"event": "test from layer"}).encode("utf-8")
    req = urllib.request.Request(
        "http://host.docker.internal:9999/api/0/envelope/",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as response:
        return "This is from the Layer"
