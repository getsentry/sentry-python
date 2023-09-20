import sentry_sdk
import time

from sentry_sdk import Hub, metrics


def parse_metrics(bytes):
    rv = []
    for line in bytes.splitlines():
        pieces = line.decode("utf-8").split("|")
        payload = pieces[0].split(":")
        name = payload[0]
        values = payload[1:]
        ty = pieces[1]
        ts = None
        tags = {}
        for piece in pieces[2:]:
            if piece[0] == "#":
                for pair in piece[1:].split(","):
                    k, v = pair.split(":", 1)
                    tags[k] = v
            elif piece[0] == "T":
                ts = int(piece[1:])
            else:
                raise ValueError("unknown piece %r" % (piece,))
        rv.append((ts, name, ty, values, tags))
    rv.sort()
    return rv


def test_incr(sentry_init, capture_envelopes):
    sentry_init(
        release="fun-release",
        environment="not-fun-env",
        _experiments={"enable_metrics": True},
    )
    envelopes = capture_envelopes()

    metrics.incr("foobar", 1.0, tags={"foo": "bar", "blub": "blah"})
    metrics.incr("foobar", 2.0, tags={"foo": "bar", "blub": "blah"})
    Hub.current.flush()

    (envelope,) = envelopes

    assert len(envelope.items) == 1
    assert envelope.items[0].headers["type"] == "statsd"
    m = parse_metrics(envelope.items[0].payload.get_bytes())

    assert len(m) == 1
    assert m[0][1] == "foobar@none"
    assert m[0][2] == "c"
    assert m[0][3] == ["3.0"]
    assert m[0][4] == {
        "blub": "blah",
        "foo": "bar",
        "release": "fun-release",
        "environment": "not-fun-env",
    }


def test_timing(sentry_init, capture_envelopes):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={"enable_metrics": True},
    )
    envelopes = capture_envelopes()

    with metrics.timing("whatever", tags={"blub": "blah"}):
        time.sleep(0.1)
    Hub.current.flush()

    (envelope,) = envelopes

    assert len(envelope.items) == 1
    assert envelope.items[0].headers["type"] == "statsd"
    m = parse_metrics(envelope.items[0].payload.get_bytes())

    assert len(m) == 1
    assert m[0][1] == "whatever@second"
    assert m[0][2] == "d"
    assert len(m[0][3]) == 1
    assert float(m[0][3][0]) >= 0.1
    assert m[0][4] == {
        "blub": "blah",
        "release": "fun-release@1.0.0",
        "environment": "not-fun-env",
    }


def test_distribution(sentry_init, capture_envelopes):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={"enable_metrics": True},
    )
    envelopes = capture_envelopes()

    metrics.distribution("dist", 1.0, tags={"a": "b"})
    metrics.distribution("dist", 2.0, tags={"a": "b"})
    metrics.distribution("dist", 2.0, tags={"a": "b"})
    metrics.distribution("dist", 3.0, tags={"a": "b"})
    Hub.current.flush()

    (envelope,) = envelopes

    assert len(envelope.items) == 1
    assert envelope.items[0].headers["type"] == "statsd"
    m = parse_metrics(envelope.items[0].payload.get_bytes())

    assert len(m) == 1
    assert m[0][1] == "dist@second"
    assert m[0][2] == "d"
    assert len(m[0][3]) == 4
    assert sorted(map(float, m[0][3])) == [1.0, 2.0, 2.0, 3.0]
    assert m[0][4] == {
        "a": "b",
        "release": "fun-release@1.0.0",
        "environment": "not-fun-env",
    }


def test_set(sentry_init, capture_envelopes):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={"enable_metrics": True},
    )
    envelopes = capture_envelopes()

    metrics.set("my-set", "peter", tags={"magic": "puff"})
    metrics.set("my-set", "paul", tags={"magic": "puff"})
    metrics.set("my-set", "mary", tags={"magic": "puff"})
    Hub.current.flush()

    (envelope,) = envelopes

    assert len(envelope.items) == 1
    assert envelope.items[0].headers["type"] == "statsd"
    m = parse_metrics(envelope.items[0].payload.get_bytes())

    assert len(m) == 1
    assert m[0][1] == "my-set@none"
    assert m[0][2] == "s"
    assert len(m[0][3]) == 3
    assert sorted(map(int, m[0][3])) == [354582103, 2513273657, 3329318813]
    assert m[0][4] == {
        "magic": "puff",
        "release": "fun-release@1.0.0",
        "environment": "not-fun-env",
    }