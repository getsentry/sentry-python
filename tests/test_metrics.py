# coding: utf-8

import time

from sentry_sdk import Hub, metrics, push_scope


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
                    old = tags.get(k)
                    if old is not None:
                        if isinstance(old, list):
                            old.append(v)
                        else:
                            tags[k] = [old, v]
                    else:
                        tags[k] = v
            elif piece[0] == "T":
                ts = int(piece[1:])
            else:
                raise ValueError("unknown piece %r" % (piece,))
        rv.append((ts, name, ty, values, tags))
    rv.sort(key=lambda x: (x[0], x[1], tuple(sorted(tags.items()))))
    return rv


def test_incr(sentry_init, capture_envelopes):
    sentry_init(
        release="fun-release",
        environment="not-fun-env",
        _experiments={"enable_metrics": True},
    )
    ts = time.time()
    envelopes = capture_envelopes()

    metrics.incr("foobar", 1.0, tags={"foo": "bar", "blub": "blah"}, timestamp=ts)
    metrics.incr("foobar", 2.0, tags={"foo": "bar", "blub": "blah"}, timestamp=ts)
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
    ts = time.time()
    envelopes = capture_envelopes()

    with metrics.timing("whatever", tags={"blub": "blah"}, timestamp=ts):
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


def test_timing_decorator(sentry_init, capture_envelopes):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={"enable_metrics": True},
    )
    envelopes = capture_envelopes()

    @metrics.timing("whatever-1", tags={"x": "y"})
    def amazing():
        time.sleep(0.1)
        return 42

    @metrics.timing("whatever-2", tags={"x": "y"}, unit="nanosecond")
    def amazing_nano():
        time.sleep(0.01)
        return 23

    assert amazing() == 42
    assert amazing_nano() == 23
    Hub.current.flush()

    (envelope,) = envelopes

    assert len(envelope.items) == 1
    assert envelope.items[0].headers["type"] == "statsd"
    m = parse_metrics(envelope.items[0].payload.get_bytes())

    assert len(m) == 2
    assert m[0][1] == "whatever-1@second"
    assert m[0][2] == "d"
    assert len(m[0][3]) == 1
    assert float(m[0][3][0]) >= 0.1
    assert m[0][4] == {
        "x": "y",
        "release": "fun-release@1.0.0",
        "environment": "not-fun-env",
    }

    assert m[1][1] == "whatever-2@nanosecond"
    assert m[1][2] == "d"
    assert len(m[1][3]) == 1
    assert float(m[1][3][0]) >= 10000000.0
    assert m[1][4] == {
        "x": "y",
        "release": "fun-release@1.0.0",
        "environment": "not-fun-env",
    }


def test_timing_basic(sentry_init, capture_envelopes):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={"enable_metrics": True},
    )
    ts = time.time()
    envelopes = capture_envelopes()

    metrics.timing("timing", 1.0, tags={"a": "b"}, timestamp=ts)
    metrics.timing("timing", 2.0, tags={"a": "b"}, timestamp=ts)
    metrics.timing("timing", 2.0, tags={"a": "b"}, timestamp=ts)
    metrics.timing("timing", 3.0, tags={"a": "b"}, timestamp=ts)
    Hub.current.flush()

    (envelope,) = envelopes

    assert len(envelope.items) == 1
    assert envelope.items[0].headers["type"] == "statsd"
    m = parse_metrics(envelope.items[0].payload.get_bytes())

    assert len(m) == 1
    assert m[0][1] == "timing@second"
    assert m[0][2] == "d"
    assert len(m[0][3]) == 4
    assert sorted(map(float, m[0][3])) == [1.0, 2.0, 2.0, 3.0]
    assert m[0][4] == {
        "a": "b",
        "release": "fun-release@1.0.0",
        "environment": "not-fun-env",
    }


def test_distribution(sentry_init, capture_envelopes):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={"enable_metrics": True},
    )
    ts = time.time()
    envelopes = capture_envelopes()

    metrics.distribution("dist", 1.0, tags={"a": "b"}, timestamp=ts)
    metrics.distribution("dist", 2.0, tags={"a": "b"}, timestamp=ts)
    metrics.distribution("dist", 2.0, tags={"a": "b"}, timestamp=ts)
    metrics.distribution("dist", 3.0, tags={"a": "b"}, timestamp=ts)
    Hub.current.flush()

    (envelope,) = envelopes

    assert len(envelope.items) == 1
    assert envelope.items[0].headers["type"] == "statsd"
    m = parse_metrics(envelope.items[0].payload.get_bytes())

    assert len(m) == 1
    assert m[0][1] == "dist@none"
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
    ts = time.time()
    envelopes = capture_envelopes()

    metrics.set("my-set", "peter", tags={"magic": "puff"}, timestamp=ts)
    metrics.set("my-set", "paul", tags={"magic": "puff"}, timestamp=ts)
    metrics.set("my-set", "mary", tags={"magic": "puff"}, timestamp=ts)
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


def test_gauge(sentry_init, capture_envelopes):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={"enable_metrics": True},
    )
    ts = time.time()
    envelopes = capture_envelopes()

    metrics.gauge("my-gauge", 10.0, tags={"x": "y"}, timestamp=ts)
    metrics.gauge("my-gauge", 20.0, tags={"x": "y"}, timestamp=ts)
    metrics.gauge("my-gauge", 30.0, tags={"x": "y"}, timestamp=ts)
    Hub.current.flush()

    (envelope,) = envelopes

    assert len(envelope.items) == 1
    assert envelope.items[0].headers["type"] == "statsd"
    m = parse_metrics(envelope.items[0].payload.get_bytes())

    assert len(m) == 1
    assert m[0][1] == "my-gauge@none"
    assert m[0][2] == "g"
    assert len(m[0][3]) == 5
    assert list(map(float, m[0][3])) == [30.0, 10.0, 30.0, 60.0, 3.0]
    assert m[0][4] == {
        "x": "y",
        "release": "fun-release@1.0.0",
        "environment": "not-fun-env",
    }


def test_multiple(sentry_init, capture_envelopes):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={"enable_metrics": True},
    )
    ts = time.time()
    envelopes = capture_envelopes()

    metrics.gauge("my-gauge", 10.0, tags={"x": "y"}, timestamp=ts)
    metrics.gauge("my-gauge", 20.0, tags={"x": "y"}, timestamp=ts)
    metrics.gauge("my-gauge", 30.0, tags={"x": "y"}, timestamp=ts)
    for _ in range(10):
        metrics.incr("counter-1", 1.0, timestamp=ts)
    metrics.incr("counter-2", 1.0, timestamp=ts)

    Hub.current.flush()

    (envelope,) = envelopes

    assert len(envelope.items) == 1
    assert envelope.items[0].headers["type"] == "statsd"
    m = parse_metrics(envelope.items[0].payload.get_bytes())

    assert len(m) == 3

    assert m[0][1] == "counter-1@none"
    assert m[0][2] == "c"
    assert list(map(float, m[0][3])) == [10.0]
    assert m[0][4] == {
        "release": "fun-release@1.0.0",
        "environment": "not-fun-env",
    }

    assert m[1][1] == "counter-2@none"
    assert m[1][2] == "c"
    assert list(map(float, m[1][3])) == [1.0]
    assert m[1][4] == {
        "release": "fun-release@1.0.0",
        "environment": "not-fun-env",
    }

    assert m[2][1] == "my-gauge@none"
    assert m[2][2] == "g"
    assert len(m[2][3]) == 5
    assert list(map(float, m[2][3])) == [30.0, 10.0, 30.0, 60.0, 3.0]
    assert m[2][4] == {
        "x": "y",
        "release": "fun-release@1.0.0",
        "environment": "not-fun-env",
    }


def test_transaction_name(sentry_init, capture_envelopes):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={"enable_metrics": True},
    )
    ts = time.time()
    envelopes = capture_envelopes()

    with push_scope() as scope:
        scope.set_transaction_name("/user/{user_id}", source="route")
        metrics.distribution("dist", 1.0, tags={"a": "b"}, timestamp=ts)
        metrics.distribution("dist", 2.0, tags={"a": "b"}, timestamp=ts)
        metrics.distribution("dist", 2.0, tags={"a": "b"}, timestamp=ts)
        metrics.distribution("dist", 3.0, tags={"a": "b"}, timestamp=ts)

    Hub.current.flush()

    (envelope,) = envelopes

    assert len(envelope.items) == 1
    assert envelope.items[0].headers["type"] == "statsd"
    m = parse_metrics(envelope.items[0].payload.get_bytes())

    assert len(m) == 1
    assert m[0][1] == "dist@none"
    assert m[0][2] == "d"
    assert len(m[0][3]) == 4
    assert sorted(map(float, m[0][3])) == [1.0, 2.0, 2.0, 3.0]
    assert m[0][4] == {
        "a": "b",
        "transaction": "/user/{user_id}",
        "release": "fun-release@1.0.0",
        "environment": "not-fun-env",
    }


def test_tag_normalization(sentry_init, capture_envelopes):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={"enable_metrics": True},
    )
    ts = time.time()
    envelopes = capture_envelopes()

    # fmt: off
    metrics.distribution("a", 1.0, tags={"foo-bar": "%$foo"}, timestamp=ts)
    metrics.distribution("b", 1.0, tags={"foo$$$bar": "blah{}"}, timestamp=ts)
    metrics.distribution("c", 1.0, tags={u"foö-bar": u"snöwmän"}, timestamp=ts)
    # fmt: on
    Hub.current.flush()

    (envelope,) = envelopes

    assert len(envelope.items) == 1
    assert envelope.items[0].headers["type"] == "statsd"
    m = parse_metrics(envelope.items[0].payload.get_bytes())

    assert len(m) == 3
    assert m[0][4] == {
        "foo-bar": "_$foo",
        "release": "fun-release@1.0.0",
        "environment": "not-fun-env",
    }

    assert m[1][4] == {
        "foo_bar": "blah{}",
        "release": "fun-release@1.0.0",
        "environment": "not-fun-env",
    }

    # fmt: off
    assert m[2][4] == {
        "fo_-bar": u"snöwmän",
        "release": "fun-release@1.0.0",
        "environment": "not-fun-env",
    }
    # fmt: on


def test_before_emit_metric(sentry_init, capture_envelopes):
    def before_emit(key, tags):
        if key == "removed-metric":
            return False
        tags["extra"] = "foo"
        del tags["release"]
        # this better be a noop!
        metrics.incr("shitty-recursion")
        return True

    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={
            "enable_metrics": True,
            "before_emit_metric": before_emit,
        },
    )
    envelopes = capture_envelopes()

    metrics.incr("removed-metric", 1.0)
    metrics.incr("actual-metric", 1.0)
    Hub.current.flush()

    (envelope,) = envelopes

    assert len(envelope.items) == 1
    assert envelope.items[0].headers["type"] == "statsd"
    m = parse_metrics(envelope.items[0].payload.get_bytes())

    assert len(m) == 1
    assert m[0][1] == "actual-metric@none"
    assert m[0][3] == ["1.0"]
    assert m[0][4] == {
        "extra": "foo",
        "environment": "not-fun-env",
    }


def test_aggregator_flush(sentry_init, capture_envelopes):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={
            "enable_metrics": True,
        },
    )
    envelopes = capture_envelopes()

    metrics.incr("a-metric", 1.0)
    Hub.current.flush()

    assert len(envelopes) == 1
    assert Hub.current.client.metrics_aggregator.buckets == {}


def test_tag_serialization(sentry_init, capture_envelopes):
    sentry_init(
        release="fun-release",
        environment="not-fun-env",
        _experiments={"enable_metrics": True},
    )
    envelopes = capture_envelopes()

    metrics.incr(
        "counter",
        tags={
            "no-value": None,
            "an-int": 42,
            "a-float": 23.0,
            "a-string": "blah",
            "more-than-one": [1, "zwei", "3.0", None],
        },
    )
    Hub.current.flush()

    (envelope,) = envelopes

    assert len(envelope.items) == 1
    assert envelope.items[0].headers["type"] == "statsd"
    m = parse_metrics(envelope.items[0].payload.get_bytes())

    assert len(m) == 1
    assert m[0][4] == {
        "an-int": "42",
        "a-float": "23.0",
        "a-string": "blah",
        "more-than-one": ["1", "3.0", "zwei"],
        "release": "fun-release",
        "environment": "not-fun-env",
    }


def test_flush_recursion_protection(sentry_init, capture_envelopes, monkeypatch):
    sentry_init(
        release="fun-release",
        environment="not-fun-env",
        _experiments={"enable_metrics": True},
    )
    envelopes = capture_envelopes()
    test_client = Hub.current.client

    real_capture_envelope = test_client.transport.capture_envelope

    def bad_capture_envelope(*args, **kwargs):
        metrics.incr("bad-metric")
        return real_capture_envelope(*args, **kwargs)

    monkeypatch.setattr(test_client.transport, "capture_envelope", bad_capture_envelope)

    metrics.incr("counter")

    # flush twice to see the inner metric
    Hub.current.flush()
    Hub.current.flush()

    (envelope,) = envelopes
    m = parse_metrics(envelope.items[0].payload.get_bytes())
    assert len(m) == 1
    assert m[0][1] == "counter@none"


def test_flush_recursion_protection_background_flush(
    sentry_init, capture_envelopes, monkeypatch
):
    monkeypatch.setattr(metrics.MetricsAggregator, "FLUSHER_SLEEP_TIME", 0.1)
    sentry_init(
        release="fun-release",
        environment="not-fun-env",
        _experiments={"enable_metrics": True},
    )
    envelopes = capture_envelopes()
    test_client = Hub.current.client

    real_capture_envelope = test_client.transport.capture_envelope

    def bad_capture_envelope(*args, **kwargs):
        metrics.incr("bad-metric")
        return real_capture_envelope(*args, **kwargs)

    monkeypatch.setattr(test_client.transport, "capture_envelope", bad_capture_envelope)

    metrics.incr("counter")

    # flush via sleep and flag
    Hub.current.client.metrics_aggregator._force_flush = True
    time.sleep(0.5)

    (envelope,) = envelopes
    m = parse_metrics(envelope.items[0].payload.get_bytes())
    assert len(m) == 1
    assert m[0][1] == "counter@none"
