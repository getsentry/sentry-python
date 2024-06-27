import sys
import time
import linecache
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk import Scope, metrics
from sentry_sdk.tracing import TRANSACTION_SOURCE_ROUTE
from sentry_sdk.envelope import parse_json

try:
    import gevent
except ImportError:
    gevent = None


minimum_python_37_with_gevent = pytest.mark.skipif(
    gevent and sys.version_info < (3, 7),
    reason="Require Python 3.7 or higher with gevent",
)


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


@minimum_python_37_with_gevent
@pytest.mark.forked
def test_increment(sentry_init, capture_envelopes, maybe_monkeypatched_threading):
    sentry_init(
        release="fun-release",
        environment="not-fun-env",
        _experiments={"enable_metrics": True, "metric_code_locations": True},
    )
    ts = time.time()
    envelopes = capture_envelopes()

    metrics.increment("foobar", 1.0, tags={"foo": "bar", "blub": "blah"}, timestamp=ts)
    # python specific alias
    metrics.incr("foobar", 2.0, tags={"foo": "bar", "blub": "blah"}, timestamp=ts)
    sentry_sdk.flush()

    (envelope,) = envelopes
    statsd_item, meta_item = envelope.items

    assert statsd_item.headers["type"] == "statsd"
    m = parse_metrics(statsd_item.payload.get_bytes())

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

    assert meta_item.headers["type"] == "metric_meta"
    assert parse_json(meta_item.payload.get_bytes()) == {
        "timestamp": mock.ANY,
        "mapping": {
            "c:foobar@none": [
                {
                    "type": "location",
                    "filename": "tests/test_metrics.py",
                    "abs_path": __file__,
                    "function": sys._getframe().f_code.co_name,
                    "module": __name__,
                    "lineno": mock.ANY,
                    "pre_context": mock.ANY,
                    "context_line": mock.ANY,
                    "post_context": mock.ANY,
                }
            ]
        },
    }


@minimum_python_37_with_gevent
@pytest.mark.forked
def test_timing(sentry_init, capture_envelopes, maybe_monkeypatched_threading):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={"enable_metrics": True, "metric_code_locations": True},
    )
    ts = time.time()
    envelopes = capture_envelopes()

    with metrics.timing("whatever", tags={"blub": "blah"}, timestamp=ts):
        time.sleep(0.1)
    sentry_sdk.flush()

    (envelope,) = envelopes
    statsd_item, meta_item = envelope.items

    assert statsd_item.headers["type"] == "statsd"
    m = parse_metrics(statsd_item.payload.get_bytes())

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

    assert meta_item.headers["type"] == "metric_meta"
    json = parse_json(meta_item.payload.get_bytes())
    assert json == {
        "timestamp": mock.ANY,
        "mapping": {
            "d:whatever@second": [
                {
                    "type": "location",
                    "filename": "tests/test_metrics.py",
                    "abs_path": __file__,
                    "function": sys._getframe().f_code.co_name,
                    "module": __name__,
                    "lineno": mock.ANY,
                    "pre_context": mock.ANY,
                    "context_line": mock.ANY,
                    "post_context": mock.ANY,
                }
            ]
        },
    }

    loc = json["mapping"]["d:whatever@second"][0]
    line = linecache.getline(loc["abs_path"], loc["lineno"])
    assert (
        line.strip()
        == 'with metrics.timing("whatever", tags={"blub": "blah"}, timestamp=ts):'
    )


@minimum_python_37_with_gevent
@pytest.mark.forked
def test_timing_decorator(
    sentry_init, capture_envelopes, maybe_monkeypatched_threading
):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={"enable_metrics": True, "metric_code_locations": True},
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
    sentry_sdk.flush()

    (envelope,) = envelopes
    statsd_item, meta_item = envelope.items

    assert statsd_item.headers["type"] == "statsd"
    m = parse_metrics(statsd_item.payload.get_bytes())

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

    assert meta_item.headers["type"] == "metric_meta"
    json = parse_json(meta_item.payload.get_bytes())
    assert json == {
        "timestamp": mock.ANY,
        "mapping": {
            "d:whatever-1@second": [
                {
                    "type": "location",
                    "filename": "tests/test_metrics.py",
                    "abs_path": __file__,
                    "function": sys._getframe().f_code.co_name,
                    "module": __name__,
                    "lineno": mock.ANY,
                    "pre_context": mock.ANY,
                    "context_line": mock.ANY,
                    "post_context": mock.ANY,
                }
            ],
            "d:whatever-2@nanosecond": [
                {
                    "type": "location",
                    "filename": "tests/test_metrics.py",
                    "abs_path": __file__,
                    "function": sys._getframe().f_code.co_name,
                    "module": __name__,
                    "lineno": mock.ANY,
                    "pre_context": mock.ANY,
                    "context_line": mock.ANY,
                    "post_context": mock.ANY,
                }
            ],
        },
    }

    # XXX: this is not the best location.  It would probably be better to
    # report the location in the function, however that is quite a bit
    # tricker to do since we report from outside the function so we really
    # only see the callsite.
    loc = json["mapping"]["d:whatever-1@second"][0]
    line = linecache.getline(loc["abs_path"], loc["lineno"])
    assert line.strip() == "assert amazing() == 42"


@minimum_python_37_with_gevent
@pytest.mark.forked
def test_timing_basic(sentry_init, capture_envelopes, maybe_monkeypatched_threading):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={"enable_metrics": True, "metric_code_locations": True},
    )
    ts = time.time()
    envelopes = capture_envelopes()

    metrics.timing("timing", 1.0, tags={"a": "b"}, timestamp=ts)
    metrics.timing("timing", 2.0, tags={"a": "b"}, timestamp=ts)
    metrics.timing("timing", 2.0, tags={"a": "b"}, timestamp=ts)
    metrics.timing("timing", 3.0, tags={"a": "b"}, timestamp=ts)
    sentry_sdk.flush()

    (envelope,) = envelopes
    statsd_item, meta_item = envelope.items

    assert statsd_item.headers["type"] == "statsd"
    m = parse_metrics(statsd_item.payload.get_bytes())

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

    assert meta_item.headers["type"] == "metric_meta"
    assert parse_json(meta_item.payload.get_bytes()) == {
        "timestamp": mock.ANY,
        "mapping": {
            "d:timing@second": [
                {
                    "type": "location",
                    "filename": "tests/test_metrics.py",
                    "abs_path": __file__,
                    "function": sys._getframe().f_code.co_name,
                    "module": __name__,
                    "lineno": mock.ANY,
                    "pre_context": mock.ANY,
                    "context_line": mock.ANY,
                    "post_context": mock.ANY,
                }
            ]
        },
    }


@minimum_python_37_with_gevent
@pytest.mark.forked
def test_distribution(sentry_init, capture_envelopes, maybe_monkeypatched_threading):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={"enable_metrics": True, "metric_code_locations": True},
    )
    ts = time.time()
    envelopes = capture_envelopes()

    metrics.distribution("dist", 1.0, tags={"a": "b"}, timestamp=ts)
    metrics.distribution("dist", 2.0, tags={"a": "b"}, timestamp=ts)
    metrics.distribution("dist", 2.0, tags={"a": "b"}, timestamp=ts)
    metrics.distribution("dist", 3.0, tags={"a": "b"}, timestamp=ts)
    sentry_sdk.flush()

    (envelope,) = envelopes
    statsd_item, meta_item = envelope.items

    assert statsd_item.headers["type"] == "statsd"
    m = parse_metrics(statsd_item.payload.get_bytes())

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

    assert meta_item.headers["type"] == "metric_meta"
    json = parse_json(meta_item.payload.get_bytes())
    assert json == {
        "timestamp": mock.ANY,
        "mapping": {
            "d:dist@none": [
                {
                    "type": "location",
                    "filename": "tests/test_metrics.py",
                    "abs_path": __file__,
                    "function": sys._getframe().f_code.co_name,
                    "module": __name__,
                    "lineno": mock.ANY,
                    "pre_context": mock.ANY,
                    "context_line": mock.ANY,
                    "post_context": mock.ANY,
                }
            ]
        },
    }

    loc = json["mapping"]["d:dist@none"][0]
    line = linecache.getline(loc["abs_path"], loc["lineno"])
    assert (
        line.strip()
        == 'metrics.distribution("dist", 1.0, tags={"a": "b"}, timestamp=ts)'
    )


@minimum_python_37_with_gevent
@pytest.mark.forked
def test_set(sentry_init, capture_envelopes, maybe_monkeypatched_threading):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={"enable_metrics": True, "metric_code_locations": True},
    )
    ts = time.time()
    envelopes = capture_envelopes()

    metrics.set("my-set", "peter", tags={"magic": "puff"}, timestamp=ts)
    metrics.set("my-set", "paul", tags={"magic": "puff"}, timestamp=ts)
    metrics.set("my-set", "mary", tags={"magic": "puff"}, timestamp=ts)
    sentry_sdk.flush()

    (envelope,) = envelopes
    statsd_item, meta_item = envelope.items

    assert statsd_item.headers["type"] == "statsd"
    m = parse_metrics(statsd_item.payload.get_bytes())

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

    assert meta_item.headers["type"] == "metric_meta"
    assert parse_json(meta_item.payload.get_bytes()) == {
        "timestamp": mock.ANY,
        "mapping": {
            "s:my-set@none": [
                {
                    "type": "location",
                    "filename": "tests/test_metrics.py",
                    "abs_path": __file__,
                    "function": sys._getframe().f_code.co_name,
                    "module": __name__,
                    "lineno": mock.ANY,
                    "pre_context": mock.ANY,
                    "context_line": mock.ANY,
                    "post_context": mock.ANY,
                }
            ]
        },
    }


@minimum_python_37_with_gevent
@pytest.mark.forked
def test_gauge(sentry_init, capture_envelopes, maybe_monkeypatched_threading):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={"enable_metrics": True, "metric_code_locations": False},
    )
    ts = time.time()
    envelopes = capture_envelopes()

    metrics.gauge("my-gauge", 10.0, tags={"x": "y"}, timestamp=ts)
    metrics.gauge("my-gauge", 20.0, tags={"x": "y"}, timestamp=ts)
    metrics.gauge("my-gauge", 30.0, tags={"x": "y"}, timestamp=ts)
    sentry_sdk.flush()

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


@minimum_python_37_with_gevent
@pytest.mark.forked
def test_multiple(sentry_init, capture_envelopes):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={"enable_metrics": True, "metric_code_locations": False},
    )
    ts = time.time()
    envelopes = capture_envelopes()

    metrics.gauge("my-gauge", 10.0, tags={"x": "y"}, timestamp=ts)
    metrics.gauge("my-gauge", 20.0, tags={"x": "y"}, timestamp=ts)
    metrics.gauge("my-gauge", 30.0, tags={"x": "y"}, timestamp=ts)
    for _ in range(10):
        metrics.increment("counter-1", 1.0, timestamp=ts)
    metrics.increment("counter-2", 1.0, timestamp=ts)

    sentry_sdk.flush()

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


@minimum_python_37_with_gevent
@pytest.mark.forked
def test_transaction_name(
    sentry_init, capture_envelopes, maybe_monkeypatched_threading
):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={"enable_metrics": True, "metric_code_locations": False},
    )
    ts = time.time()
    envelopes = capture_envelopes()

    scope = Scope.get_current_scope()
    scope.set_transaction_name("/user/{user_id}", source="route")
    metrics.distribution("dist", 1.0, tags={"a": "b"}, timestamp=ts)
    metrics.distribution("dist", 2.0, tags={"a": "b"}, timestamp=ts)
    metrics.distribution("dist", 2.0, tags={"a": "b"}, timestamp=ts)
    metrics.distribution("dist", 3.0, tags={"a": "b"}, timestamp=ts)

    sentry_sdk.flush()

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


@minimum_python_37_with_gevent
@pytest.mark.forked
def test_metric_summaries(
    sentry_init, capture_envelopes, maybe_monkeypatched_threading
):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        enable_tracing=True,
    )
    ts = time.time()
    envelopes = capture_envelopes()

    with sentry_sdk.start_transaction(
        op="stuff", name="/foo", source=TRANSACTION_SOURCE_ROUTE
    ) as transaction:
        metrics.increment("root-counter", timestamp=ts)
        with metrics.timing("my-timer-metric", tags={"a": "b"}, timestamp=ts):
            for x in range(10):
                metrics.distribution("my-dist", float(x), timestamp=ts)

    sentry_sdk.flush()

    (transaction, envelope) = envelopes

    # Metrics Emission
    assert envelope.items[0].headers["type"] == "statsd"
    m = parse_metrics(envelope.items[0].payload.get_bytes())

    assert len(m) == 3

    assert m[0][1] == "my-dist@none"
    assert m[0][2] == "d"
    assert len(m[0][3]) == 10
    assert sorted(m[0][3]) == list(map(str, map(float, range(10))))
    assert m[0][4] == {
        "transaction": "/foo",
        "release": "fun-release@1.0.0",
        "environment": "not-fun-env",
    }

    assert m[1][1] == "my-timer-metric@second"
    assert m[1][2] == "d"
    assert len(m[1][3]) == 1
    assert m[1][4] == {
        "a": "b",
        "transaction": "/foo",
        "release": "fun-release@1.0.0",
        "environment": "not-fun-env",
    }

    assert m[2][1] == "root-counter@none"
    assert m[2][2] == "c"
    assert m[2][3] == ["1.0"]
    assert m[2][4] == {
        "transaction": "/foo",
        "release": "fun-release@1.0.0",
        "environment": "not-fun-env",
    }

    # Measurement Attachment
    t = transaction.items[0].get_transaction_event()

    assert t["_metrics_summary"] == {
        "c:root-counter@none": [
            {
                "count": 1,
                "min": 1.0,
                "max": 1.0,
                "sum": 1.0,
                "tags": {
                    "transaction": "/foo",
                    "release": "fun-release@1.0.0",
                    "environment": "not-fun-env",
                },
            }
        ]
    }

    assert t["spans"][0]["_metrics_summary"]["d:my-dist@none"] == [
        {
            "count": 10,
            "min": 0.0,
            "max": 9.0,
            "sum": 45.0,
            "tags": {
                "environment": "not-fun-env",
                "release": "fun-release@1.0.0",
                "transaction": "/foo",
            },
        }
    ]

    assert t["spans"][0]["tags"] == {"a": "b"}
    (timer,) = t["spans"][0]["_metrics_summary"]["d:my-timer-metric@second"]
    assert timer["count"] == 1
    assert timer["max"] == timer["min"] == timer["sum"]
    assert timer["sum"] > 0
    assert timer["tags"] == {
        "a": "b",
        "environment": "not-fun-env",
        "release": "fun-release@1.0.0",
        "transaction": "/foo",
    }


@minimum_python_37_with_gevent
@pytest.mark.forked
@pytest.mark.parametrize(
    "metric_name,metric_unit,expected_name",
    [
        ("first-metric", "nano-second", "first-metric@nanosecond"),
        ("another_metric?", "nano second", "another_metric_@nanosecond"),
        (
            "metric",
            "nanosecond",
            "metric@nanosecond",
        ),
        (
            "my.amaze.metric I guess",
            "nano|\nsecond",
            "my.amaze.metric_I_guess@nanosecond",
        ),
        ("métríc", "nanöseconď", "m_tr_c@nansecon"),
    ],
)
def test_metric_name_normalization(
    sentry_init,
    capture_envelopes,
    metric_name,
    metric_unit,
    expected_name,
    maybe_monkeypatched_threading,
):
    sentry_init(
        _experiments={"enable_metrics": True, "metric_code_locations": False},
    )
    envelopes = capture_envelopes()

    metrics.distribution(metric_name, 1.0, unit=metric_unit)

    sentry_sdk.flush()

    (envelope,) = envelopes

    assert len(envelope.items) == 1
    assert envelope.items[0].headers["type"] == "statsd"

    parsed_metrics = parse_metrics(envelope.items[0].payload.get_bytes())
    assert len(parsed_metrics) == 1

    name = parsed_metrics[0][1]
    assert name == expected_name


@minimum_python_37_with_gevent
@pytest.mark.forked
@pytest.mark.parametrize(
    "metric_tag,expected_tag",
    [
        ({"f-oo|bar": "%$foo/"}, {"f-oobar": "%$foo/"}),
        ({"foo$.$.$bar": "blah{}"}, {"foo..bar": "blah{}"}),
        (
            {"foö-bar": "snöwmän"},
            {"fo-bar": "snöwmän"},
        ),
        ({"route": "GET /foo"}, {"route": "GET /foo"}),
        ({"__bar__": "this | or , that"}, {"__bar__": "this \\u{7c} or \\u{2c} that"}),
        ({"foo/": "hello!\n\r\t\\"}, {"foo/": "hello!\\n\\r\\t\\\\"}),
    ],
)
def test_metric_tag_normalization(
    sentry_init,
    capture_envelopes,
    metric_tag,
    expected_tag,
    maybe_monkeypatched_threading,
):
    sentry_init(
        _experiments={"enable_metrics": True, "metric_code_locations": False},
    )
    envelopes = capture_envelopes()

    metrics.distribution("a", 1.0, tags=metric_tag)

    sentry_sdk.flush()

    (envelope,) = envelopes

    assert len(envelope.items) == 1
    assert envelope.items[0].headers["type"] == "statsd"

    parsed_metrics = parse_metrics(envelope.items[0].payload.get_bytes())
    assert len(parsed_metrics) == 1

    tags = parsed_metrics[0][4]

    expected_tag_key, expected_tag_value = expected_tag.popitem()
    assert expected_tag_key in tags
    assert tags[expected_tag_key] == expected_tag_value


@minimum_python_37_with_gevent
@pytest.mark.forked
def test_before_emit_metric(
    sentry_init, capture_envelopes, maybe_monkeypatched_threading
):
    def before_emit(key, value, unit, tags):
        if key == "removed-metric" or value == 47 or unit == "unsupported":
            return False

        tags["extra"] = "foo"
        del tags["release"]
        # this better be a noop!
        metrics.increment("shitty-recursion")
        return True

    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={
            "enable_metrics": True,
            "metric_code_locations": False,
            "before_emit_metric": before_emit,
        },
    )
    envelopes = capture_envelopes()

    metrics.increment("removed-metric", 1.0)
    metrics.increment("another-removed-metric", 47)
    metrics.increment("yet-another-removed-metric", 1.0, unit="unsupported")
    metrics.increment("actual-metric", 1.0)
    sentry_sdk.flush()

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


@minimum_python_37_with_gevent
@pytest.mark.forked
def test_aggregator_flush(
    sentry_init, capture_envelopes, maybe_monkeypatched_threading
):
    sentry_init(
        release="fun-release@1.0.0",
        environment="not-fun-env",
        _experiments={
            "enable_metrics": True,
        },
    )
    envelopes = capture_envelopes()

    metrics.increment("a-metric", 1.0)
    sentry_sdk.flush()

    assert len(envelopes) == 1
    assert sentry_sdk.get_client().metrics_aggregator.buckets == {}


@minimum_python_37_with_gevent
@pytest.mark.forked
def test_tag_serialization(
    sentry_init, capture_envelopes, maybe_monkeypatched_threading
):
    sentry_init(
        release="fun-release",
        environment="not-fun-env",
        _experiments={"enable_metrics": True, "metric_code_locations": False},
    )
    envelopes = capture_envelopes()

    metrics.increment(
        "counter",
        tags={
            "no-value": None,
            "an-int": 42,
            "a-float": 23.0,
            "a-string": "blah",
            "more-than-one": [1, "zwei", "3.0", None],
        },
    )
    sentry_sdk.flush()

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


@minimum_python_37_with_gevent
@pytest.mark.forked
def test_flush_recursion_protection(
    sentry_init, capture_envelopes, monkeypatch, maybe_monkeypatched_threading
):
    sentry_init(
        release="fun-release",
        environment="not-fun-env",
        _experiments={"enable_metrics": True},
    )
    envelopes = capture_envelopes()
    test_client = sentry_sdk.get_client()

    real_capture_envelope = test_client.transport.capture_envelope

    def bad_capture_envelope(*args, **kwargs):
        metrics.increment("bad-metric")
        return real_capture_envelope(*args, **kwargs)

    monkeypatch.setattr(test_client.transport, "capture_envelope", bad_capture_envelope)

    metrics.increment("counter")

    # flush twice to see the inner metric
    sentry_sdk.flush()
    sentry_sdk.flush()

    (envelope,) = envelopes
    m = parse_metrics(envelope.items[0].payload.get_bytes())
    assert len(m) == 1
    assert m[0][1] == "counter@none"


@minimum_python_37_with_gevent
@pytest.mark.forked
def test_flush_recursion_protection_background_flush(
    sentry_init, capture_envelopes, monkeypatch, maybe_monkeypatched_threading
):
    monkeypatch.setattr(metrics.MetricsAggregator, "FLUSHER_SLEEP_TIME", 0.01)
    sentry_init(
        release="fun-release",
        environment="not-fun-env",
        _experiments={"enable_metrics": True},
    )
    envelopes = capture_envelopes()
    test_client = sentry_sdk.get_client()

    real_capture_envelope = test_client.transport.capture_envelope

    def bad_capture_envelope(*args, **kwargs):
        metrics.increment("bad-metric")
        return real_capture_envelope(*args, **kwargs)

    monkeypatch.setattr(test_client.transport, "capture_envelope", bad_capture_envelope)

    metrics.increment("counter")

    # flush via sleep and flag
    sentry_sdk.get_client().metrics_aggregator._force_flush = True
    time.sleep(0.5)

    (envelope,) = envelopes
    m = parse_metrics(envelope.items[0].payload.get_bytes())
    assert len(m) == 1
    assert m[0][1] == "counter@none"


@pytest.mark.skipif(
    not gevent or sys.version_info >= (3, 7),
    reason="Python 3.6 or lower and gevent required",
)
@pytest.mark.forked
def test_disable_metrics_for_old_python_with_gevent(
    sentry_init, capture_envelopes, maybe_monkeypatched_threading
):
    if maybe_monkeypatched_threading != "greenlet":
        pytest.skip("Test specifically for gevent/greenlet")

    sentry_init(
        release="fun-release",
        environment="not-fun-env",
        _experiments={"enable_metrics": True},
    )
    envelopes = capture_envelopes()

    metrics.incr("counter")

    sentry_sdk.flush()

    assert sentry_sdk.get_client().metrics_aggregator is None
    assert not envelopes
