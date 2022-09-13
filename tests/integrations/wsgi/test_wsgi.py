from werkzeug.test import Client

import pytest

import sentry_sdk
from sentry_sdk.integrations.wsgi import SentryWsgiMiddleware
from sentry_sdk.profiler import teardown_profiler
from collections import Counter

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


@pytest.fixture
def crashing_app():
    def app(environ, start_response):
        1 / 0

    return app


@pytest.fixture
def profiling():
    yield
    teardown_profiler()


class IterableApp(object):
    def __init__(self, iterable):
        self.iterable = iterable

    def __call__(self, environ, start_response):
        return self.iterable


class ExitingIterable(object):
    def __init__(self, exc_func):
        self._exc_func = exc_func

    def __iter__(self):
        return self

    def __next__(self):
        raise self._exc_func()

    def next(self):
        return type(self).__next__(self)


def test_basic(sentry_init, crashing_app, capture_events):
    sentry_init(send_default_pii=True)
    app = SentryWsgiMiddleware(crashing_app)
    client = Client(app)
    events = capture_events()

    with pytest.raises(ZeroDivisionError):
        client.get("/")

    (event,) = events

    assert event["transaction"] == "generic WSGI request"

    assert event["request"] == {
        "env": {"SERVER_NAME": "localhost", "SERVER_PORT": "80"},
        "headers": {"Host": "localhost"},
        "method": "GET",
        "query_string": "",
        "url": "http://localhost/",
    }


@pytest.fixture(params=[0, None])
def test_systemexit_zero_is_ignored(sentry_init, capture_events, request):
    zero_code = request.param
    sentry_init(send_default_pii=True)
    iterable = ExitingIterable(lambda: SystemExit(zero_code))
    app = SentryWsgiMiddleware(IterableApp(iterable))
    client = Client(app)
    events = capture_events()

    with pytest.raises(SystemExit):
        client.get("/")

    assert len(events) == 0


@pytest.fixture(params=["", "foo", 1, 2])
def test_systemexit_nonzero_is_captured(sentry_init, capture_events, request):
    nonzero_code = request.param
    sentry_init(send_default_pii=True)
    iterable = ExitingIterable(lambda: SystemExit(nonzero_code))
    app = SentryWsgiMiddleware(IterableApp(iterable))
    client = Client(app)
    events = capture_events()

    with pytest.raises(SystemExit):
        client.get("/")

    (event,) = events

    assert "exception" in event
    exc = event["exception"]["values"][-1]
    assert exc["type"] == "SystemExit"
    assert exc["value"] == nonzero_code
    assert event["level"] == "error"


def test_keyboard_interrupt_is_captured(sentry_init, capture_events):
    sentry_init(send_default_pii=True)
    iterable = ExitingIterable(lambda: KeyboardInterrupt())
    app = SentryWsgiMiddleware(IterableApp(iterable))
    client = Client(app)
    events = capture_events()

    with pytest.raises(KeyboardInterrupt):
        client.get("/")

    (event,) = events

    assert "exception" in event
    exc = event["exception"]["values"][-1]
    assert exc["type"] == "KeyboardInterrupt"
    assert exc["value"] == ""
    assert event["level"] == "error"


def test_transaction_with_error(
    sentry_init, crashing_app, capture_events, DictionaryContaining  # noqa:N803
):
    def dogpark(environ, start_response):
        raise Exception("Fetch aborted. The ball was not returned.")

    sentry_init(send_default_pii=True, traces_sample_rate=1.0)
    app = SentryWsgiMiddleware(dogpark)
    client = Client(app)
    events = capture_events()

    with pytest.raises(Exception):
        client.get("http://dogs.are.great/sit/stay/rollover/")

    error_event, envelope = events

    assert error_event["transaction"] == "generic WSGI request"
    assert error_event["contexts"]["trace"]["op"] == "http.server"
    assert error_event["exception"]["values"][0]["type"] == "Exception"
    assert (
        error_event["exception"]["values"][0]["value"]
        == "Fetch aborted. The ball was not returned."
    )

    assert envelope["type"] == "transaction"

    # event trace context is a subset of envelope trace context
    assert envelope["contexts"]["trace"] == DictionaryContaining(
        error_event["contexts"]["trace"]
    )
    assert envelope["contexts"]["trace"]["status"] == "internal_error"
    assert envelope["transaction"] == error_event["transaction"]
    assert envelope["request"] == error_event["request"]


def test_transaction_no_error(
    sentry_init, capture_events, DictionaryContaining  # noqa:N803
):
    def dogpark(environ, start_response):
        start_response("200 OK", [])
        return ["Go get the ball! Good dog!"]

    sentry_init(send_default_pii=True, traces_sample_rate=1.0)
    app = SentryWsgiMiddleware(dogpark)
    client = Client(app)
    events = capture_events()

    client.get("/dogs/are/great/")

    envelope = events[0]

    assert envelope["type"] == "transaction"
    assert envelope["transaction"] == "generic WSGI request"
    assert envelope["contexts"]["trace"]["op"] == "http.server"
    assert envelope["request"] == DictionaryContaining(
        {"method": "GET", "url": "http://localhost/dogs/are/great/"}
    )


def test_traces_sampler_gets_correct_values_in_sampling_context(
    sentry_init, DictionaryContaining, ObjectDescribedBy  # noqa:N803
):
    def app(environ, start_response):
        start_response("200 OK", [])
        return ["Go get the ball! Good dog!"]

    traces_sampler = mock.Mock(return_value=True)
    sentry_init(send_default_pii=True, traces_sampler=traces_sampler)
    app = SentryWsgiMiddleware(app)
    client = Client(app)

    client.get("/dogs/are/great/")

    traces_sampler.assert_any_call(
        DictionaryContaining(
            {
                "wsgi_environ": DictionaryContaining(
                    {
                        "PATH_INFO": "/dogs/are/great/",
                        "REQUEST_METHOD": "GET",
                    },
                ),
            }
        )
    )


def test_session_mode_defaults_to_request_mode_in_wsgi_handler(
    capture_envelopes, sentry_init
):
    """
    Test that ensures that even though the default `session_mode` for
    auto_session_tracking is `application`, that flips to `request` when we are
    in the WSGI handler
    """

    def app(environ, start_response):
        start_response("200 OK", [])
        return ["Go get the ball! Good dog!"]

    traces_sampler = mock.Mock(return_value=True)
    sentry_init(send_default_pii=True, traces_sampler=traces_sampler)
    app = SentryWsgiMiddleware(app)
    envelopes = capture_envelopes()

    client = Client(app)

    client.get("/dogs/are/great/")

    sentry_sdk.flush()

    sess = envelopes[1]
    assert len(sess.items) == 1
    sess_event = sess.items[0].payload.json

    aggregates = sess_event["aggregates"]
    assert len(aggregates) == 1
    assert aggregates[0]["exited"] == 1


def test_auto_session_tracking_with_aggregates(sentry_init, capture_envelopes):
    """
    Test for correct session aggregates in auto session tracking.
    """

    def sample_app(environ, start_response):
        if environ["REQUEST_URI"] != "/dogs/are/great/":
            1 / 0

        start_response("200 OK", [])
        return ["Go get the ball! Good dog!"]

    traces_sampler = mock.Mock(return_value=True)
    sentry_init(send_default_pii=True, traces_sampler=traces_sampler)
    app = SentryWsgiMiddleware(sample_app)
    envelopes = capture_envelopes()
    assert len(envelopes) == 0

    client = Client(app)
    client.get("/dogs/are/great/")
    client.get("/dogs/are/great/")
    try:
        client.get("/trigger/an/error/")
    except ZeroDivisionError:
        pass

    sentry_sdk.flush()

    count_item_types = Counter()
    for envelope in envelopes:
        count_item_types[envelope.items[0].type] += 1

    assert count_item_types["transaction"] == 3
    assert count_item_types["event"] == 1
    assert count_item_types["sessions"] == 1
    assert len(envelopes) == 5

    session_aggregates = envelopes[-1].items[0].payload.json["aggregates"]
    assert session_aggregates[0]["exited"] == 2
    assert session_aggregates[0]["crashed"] == 1
    assert len(session_aggregates) == 1


@pytest.mark.parametrize(
    "profiles_sample_rate,should_send",
    [(1.0, True), (0.75, True), (0.25, False), (None, False)],
)
def test_profile_sent_when_profiling_enabled(
    capture_envelopes, sentry_init, profiling, profiles_sample_rate, should_send
):
    def test_app(environ, start_response):
        start_response("200 OK", [])
        return ["Go get the ball! Good dog!"]

    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"profiles_sample_rate": profiles_sample_rate},
    )
    app = SentryWsgiMiddleware(test_app)
    envelopes = capture_envelopes()

    with mock.patch("sentry_sdk.profiler.random.random", return_value=0.5):
        client = Client(app)
        client.get("/")

    profile_sent = False
    for item in envelopes[0].items:
        if item.headers["type"] == "profile":
            profile_sent = True
            break
    assert profile_sent == should_send
