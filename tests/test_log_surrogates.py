import json

from sentry_sdk._log_batcher import LogBatcher


def test_lone_surrogate_does_not_survive_into_the_log_payload():
    name = b"report-\xff.csv".decode("utf-8", "surrogateescape")
    pair = "ok-\U0001f600"
    item = {
        "time_unix_nano": 1_000_000_000,
        "severity_text": "info",
        "severity_number": 9,
        "body": f"opened {name}",
        "attributes": {
            name: name,
            "names": [name, pair],
            "count": 1,
        },
    }

    out = LogBatcher._to_transport_format(item)
    payload = json.dumps(out)

    assert "\\udcff" not in payload
    assert "\ufffd" in out["body"]
    assert "\udcff" not in out["attributes"]["names"]["value"][0]
    assert out["attributes"]["names"]["value"][1] == pair
    assert out["attributes"]["count"] == {"value": 1, "type": "integer"}
    assert all("\\udcff" not in json.dumps(key) for key in out["attributes"])


def test_valid_surrogate_pair_in_a_log_body_is_kept():
    pair = "\U0001f600"
    out = LogBatcher._to_transport_format(
        {
            "time_unix_nano": 1,
            "severity_text": "info",
            "severity_number": 9,
            "body": pair,
            "attributes": {},
        }
    )

    assert out["body"] == pair
