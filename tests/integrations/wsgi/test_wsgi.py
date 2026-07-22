from collections import Counter
from unittest import mock

import pytest
from werkzeug.test import Client

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.integrations.wsgi import (
    SentryWsgiMiddleware,
    _ScopedResponse,
    get_request_url,
)


@pytest.fixture
def crashing_app():
    def app(environ, start_response):
        1 / 0

    return app


class IterableApp:
    def __init__(self, iterable):
        self.iterable = iterable

    def __call__(self, environ, start_response):
        return self.iterable


class ExitingIterable:
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


@pytest.mark.parametrize("path_info", ("bark/", "/bark/"))
@pytest.mark.parametrize("script_name", ("woof/woof", "woof/woof/"))
def test_script_name_is_respected(
    sentry_init, crashing_app, capture_events, script_name, path_info
):
    sentry_init(send_default_pii=True)
    app = SentryWsgiMiddleware(crashing_app)
    client = Client(app)
    events = capture_events()

    with pytest.raises(ZeroDivisionError):
        # setting url with PATH_INFO: bark/, HTTP_HOST: dogs.are.great and SCRIPT_NAME: woof/woof/
        client.get(path_info, f"https://dogs.are.great/{script_name}")  # noqa: E231

    (event,) = events

    assert event["request"]["url"] == "https://dogs.are.great/woof/woof/bark/"


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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_transaction_with_error(
    sentry_init,
    crashing_app,
    capture_events,
    capture_items,
    DictionaryContaining,  # noqa:N803
    span_streaming,
):
    def dogpark(environ, start_response):
        raise ValueError("Fetch aborted. The ball was not returned.")

    sentry_init(
        send_default_pii=True,
        traces_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )
    app = SentryWsgiMiddleware(dogpark)
    client = Client(app)

    if span_streaming:
        items = capture_items("event", "span")
    else:
        events = capture_events()

    with pytest.raises(ValueError):
        client.get("http://dogs.are.great/sit/stay/rollover/")

    sentry_sdk.flush()

    if span_streaming:
        assert len(items) == 2
        assert items[0].type == "event"
        assert items[1].type == "span"

        error_event = items[0].payload
        span_item = items[1].payload
    else:
        error_event, envelope = events

        assert error_event["transaction"] == "generic WSGI request"

    assert error_event["contexts"]["trace"]["op"] == "http.server"
    assert error_event["exception"]["values"][0]["type"] == "ValueError"
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "wsgi"
    assert error_event["exception"]["values"][0]["mechanism"]["handled"] is False
    assert (
        error_event["exception"]["values"][0]["value"]
        == "Fetch aborted. The ball was not returned."
    )

    if span_streaming:
        assert span_item["trace_id"] == error_event["contexts"]["trace"]["trace_id"]
        assert span_item["span_id"] == error_event["contexts"]["trace"]["span_id"]
        assert span_item["status"] == "error"
    else:
        assert envelope["type"] == "transaction"

        # event trace context is a subset of envelope trace context
        assert envelope["contexts"]["trace"] == DictionaryContaining(
            error_event["contexts"]["trace"]
        )
        assert envelope["contexts"]["trace"]["status"] == "internal_error"
        assert envelope["transaction"] == error_event["transaction"]
        assert envelope["request"] == error_event["request"]


@pytest.mark.parametrize("send_pii", [True, False])
@pytest.mark.parametrize("span_streaming", [True, False])
def test_transaction_no_error(
    sentry_init,
    capture_events,
    capture_items,
    DictionaryContaining,  # noqa:N803
    span_streaming,
    send_pii,
):
    def dogpark(environ, start_response):
        start_response("200 OK", [])
        return ["Go get the ball! Good dog!"]

    sentry_init(
        send_default_pii=send_pii,
        traces_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )
    app = SentryWsgiMiddleware(dogpark)
    client = Client(app)

    if span_streaming:
        items = capture_items("span")
    else:
        events = capture_events()

    client.get("/dogs/are/great?toy=tennisball")

    sentry_sdk.flush()

    if span_streaming:
        assert len(items) == 1
        span = items[0].payload

        assert span["is_segment"] is True
        assert span["name"] == "generic WSGI request"
        assert span["attributes"]["sentry.op"] == "http.server"
        assert span["attributes"]["sentry.segment.name.source"] == "route"
        assert span["attributes"]["http.request.method"] == "GET"
        assert span["attributes"]["http.response.status_code"] == 200
        assert span["status"] == "ok"

        if send_pii:
            assert span["attributes"]["url.full"] == "http://localhost/dogs/are/great"
            assert span["attributes"]["url.path"] == "/dogs/are/great"
            assert span["attributes"]["http.query"] == "toy=tennisball"
        else:
            assert "url.path" not in span["attributes"]
            assert "url.full" not in span["attributes"]
            assert "http.query" not in span["attributes"]

    else:
        envelope = events[0]

        assert envelope["type"] == "transaction"
        assert envelope["transaction"] == "generic WSGI request"
        assert envelope["contexts"]["trace"]["op"] == "http.server"
        assert envelope["request"] == DictionaryContaining(
            {
                "method": "GET",
                "url": "http://localhost/dogs/are/great",
                "query_string": "toy=tennisball",
            }
        )


@pytest.mark.parametrize("span_streaming", [True, False])
def test_has_trace_if_performance_enabled(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    def dogpark(environ, start_response):
        capture_message("Attempting to fetch the ball")
        raise ValueError("Fetch aborted. The ball was not returned.")

    sentry_init(
        traces_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )
    app = SentryWsgiMiddleware(dogpark)
    client = Client(app)

    if span_streaming:
        items = capture_items("event", "span")
    else:
        events = capture_events()

    with pytest.raises(ValueError):
        client.get("http://dogs.are.great/sit/stay/rollover/")

    sentry_sdk.flush()

    if span_streaming:
        msg_event, error_event, span_item = items

        assert msg_event.type == "event"
        msg_event = msg_event.payload
        assert msg_event["contexts"]["trace"]
        assert "trace_id" in msg_event["contexts"]["trace"]

        assert error_event.type == "event"
        error_event = error_event.payload
        assert error_event["contexts"]["trace"]
        assert "trace_id" in error_event["contexts"]["trace"]

        assert span_item.type == "span"
        span_item = span_item.payload
        assert span_item["trace_id"] is not None

        assert (
            msg_event["contexts"]["trace"]["trace_id"]
            == error_event["contexts"]["trace"]["trace_id"]
            == span_item["trace_id"]
        )
    else:
        msg_event, error_event, transaction_event = events

        assert msg_event["contexts"]["trace"]
        assert "trace_id" in msg_event["contexts"]["trace"]

        assert error_event["contexts"]["trace"]
        assert "trace_id" in error_event["contexts"]["trace"]

        assert transaction_event["contexts"]["trace"]
        assert "trace_id" in transaction_event["contexts"]["trace"]

        assert (
            msg_event["contexts"]["trace"]["trace_id"]
            == error_event["contexts"]["trace"]["trace_id"]
            == transaction_event["contexts"]["trace"]["trace_id"]
        )


def test_has_trace_if_performance_disabled(
    sentry_init,
    capture_events,
):
    def dogpark(environ, start_response):
        capture_message("Attempting to fetch the ball")
        raise ValueError("Fetch aborted. The ball was not returned.")

    sentry_init()
    app = SentryWsgiMiddleware(dogpark)
    client = Client(app)
    events = capture_events()

    with pytest.raises(ValueError):
        client.get("http://dogs.are.great/sit/stay/rollover/")

    msg_event, error_event = events

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]


@pytest.mark.parametrize("span_streaming", [True, False])
def test_trace_from_headers_if_performance_enabled(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    def dogpark(environ, start_response):
        capture_message("Attempting to fetch the ball")
        raise ValueError("Fetch aborted. The ball was not returned.")

    sentry_init(
        traces_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )
    app = SentryWsgiMiddleware(dogpark)
    client = Client(app)

    if span_streaming:
        items = capture_items("event", "span")
    else:
        events = capture_events()

    trace_id = "582b43a4192642f0b136d5159a501701"
    sentry_trace_header = "{}-{}-{}".format(trace_id, "6e8f22c393e68f19", 1)

    with pytest.raises(ValueError):
        client.get(
            "http://dogs.are.great/sit/stay/rollover/",
            headers={"sentry-trace": sentry_trace_header},
        )

    sentry_sdk.flush()

    if span_streaming:
        msg_event, error_event, span_item = items

        assert msg_event.payload["contexts"]["trace"]["trace_id"] == trace_id
        assert error_event.payload["contexts"]["trace"]["trace_id"] == trace_id
        assert span_item.payload["trace_id"] == trace_id
    else:
        msg_event, error_event, transaction_event = events

        assert msg_event["contexts"]["trace"]
        assert "trace_id" in msg_event["contexts"]["trace"]

        assert error_event["contexts"]["trace"]
        assert "trace_id" in error_event["contexts"]["trace"]

        assert transaction_event["contexts"]["trace"]
        assert "trace_id" in transaction_event["contexts"]["trace"]

        assert msg_event["contexts"]["trace"]["trace_id"] == trace_id
        assert error_event["contexts"]["trace"]["trace_id"] == trace_id
        assert transaction_event["contexts"]["trace"]["trace_id"] == trace_id


def test_trace_from_headers_if_performance_disabled(
    sentry_init,
    capture_events,
):
    def dogpark(environ, start_response):
        capture_message("Attempting to fetch the ball")
        raise ValueError("Fetch aborted. The ball was not returned.")

    sentry_init()
    app = SentryWsgiMiddleware(dogpark)
    client = Client(app)
    events = capture_events()

    trace_id = "582b43a4192642f0b136d5159a501701"
    sentry_trace_header = "{}-{}-{}".format(trace_id, "6e8f22c393e68f19", 1)

    with pytest.raises(ValueError):
        client.get(
            "http://dogs.are.great/sit/stay/rollover/",
            headers={"sentry-trace": sentry_trace_header},
        )

    msg_event, error_event = events

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]
    assert msg_event["contexts"]["trace"]["trace_id"] == trace_id

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]
    assert error_event["contexts"]["trace"]["trace_id"] == trace_id


@pytest.mark.parametrize("span_streaming", [True, False])
def test_traces_sampler_gets_correct_values_in_sampling_context(
    sentry_init,
    DictionaryContaining,  # noqa:N803
    span_streaming,
):
    def app(environ, start_response):
        start_response("200 OK", [])
        return ["Go get the ball! Good dog!"]

    traces_sampler = mock.Mock(return_value=True)
    sentry_init(
        send_default_pii=True,
        traces_sampler=traces_sampler,
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )
    app = SentryWsgiMiddleware(app)
    client = Client(app)

    client.get("/dogs/are/great/")

    traces_sampler.assert_any_call(
        DictionaryContaining(
            {
                "transaction_context": DictionaryContaining(
                    {
                        "name": "generic WSGI request",
                    },
                ),
                "wsgi_environ": DictionaryContaining(
                    {
                        "PATH_INFO": "/dogs/are/great/",
                        "REQUEST_METHOD": "GET",
                    },
                ),
            }
        )
    )


@pytest.mark.parametrize("span_streaming", [True, False])
def test_session_mode_defaults_to_request_mode_in_wsgi_handler(
    capture_envelopes, sentry_init, span_streaming
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
    sentry_init(
        send_default_pii=True,
        traces_sampler=traces_sampler,
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )
    app = SentryWsgiMiddleware(app)
    envelopes = capture_envelopes()

    client = Client(app)

    client.get("/dogs/are/great/")

    sentry_sdk.flush()

    session_envelopes = [
        e for e in envelopes if any(item.type == "sessions" for item in e.items)
    ]
    assert len(session_envelopes) == 1
    sess = session_envelopes[0]
    assert len(sess.items) == 1
    sess_event = sess.items[0].payload.json

    aggregates = sess_event["aggregates"]
    assert len(aggregates) == 1
    assert aggregates[0]["exited"] == 1


@pytest.mark.parametrize("span_streaming", [True, False])
def test_auto_session_tracking_with_aggregates(
    sentry_init, capture_envelopes, span_streaming
):
    """
    Test for correct session aggregates in auto session tracking.
    """

    def sample_app(environ, start_response):
        if environ["REQUEST_URI"] != "/dogs/are/great/":
            1 / 0

        start_response("200 OK", [])
        return ["Go get the ball! Good dog!"]

    traces_sampler = mock.Mock(return_value=True)
    sentry_init(
        send_default_pii=True,
        traces_sampler=traces_sampler,
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )
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
        for item in envelope.items:
            count_item_types[item.type] += 1

    if span_streaming:
        assert count_item_types["span"] == 3
    else:
        assert count_item_types["transaction"] == 3
    assert count_item_types["event"] == 1
    assert count_item_types["sessions"] == 1

    session_envelopes = [
        e for e in envelopes if any(item.type == "sessions" for item in e.items)
    ]
    assert len(session_envelopes) == 1
    session_aggregates = session_envelopes[0].items[0].payload.json["aggregates"]
    # Sum across buckets: sessions split by minute-truncated start time.
    assert sum(agg.get("exited", 0) for agg in session_aggregates) == 2
    assert sum(agg.get("crashed", 0) for agg in session_aggregates) == 1


@mock.patch("sentry_sdk.profiler.transaction_profiler.PROFILE_MINIMUM_SAMPLES", 0)
def test_profile_sent(
    sentry_init,
    capture_envelopes,
    teardown_profiling,
):
    def test_app(environ, start_response):
        start_response("200 OK", [])
        return ["Go get the ball! Good dog!"]

    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"profiles_sample_rate": 1.0},
    )
    app = SentryWsgiMiddleware(test_app)
    envelopes = capture_envelopes()

    client = Client(app)
    client.get("/")

    envelopes = [envelope for envelope in envelopes]
    assert len(envelopes) == 1

    profiles = [item for item in envelopes[0].items if item.type == "profile"]
    assert len(profiles) == 1


@pytest.mark.parametrize("span_streaming", [True, False])
def test_span_origin_manual(sentry_init, capture_events, capture_items, span_streaming):
    def dogpark(environ, start_response):
        start_response("200 OK", [])
        return ["Go get the ball! Good dog!"]

    sentry_init(
        send_default_pii=True,
        traces_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )
    app = SentryWsgiMiddleware(dogpark)

    if span_streaming:
        items = capture_items("span")
    else:
        events = capture_events()

    client = Client(app)
    client.get("/dogs/are/great/")

    sentry_sdk.flush()

    if span_streaming:
        assert len(items) == 1
        assert items[0].payload["attributes"]["sentry.origin"] == "manual"
    else:
        (event,) = events
        assert event["contexts"]["trace"]["origin"] == "manual"


@pytest.mark.parametrize("span_streaming", [True, False])
def test_span_origin_custom(sentry_init, capture_events, capture_items, span_streaming):
    def dogpark(environ, start_response):
        start_response("200 OK", [])
        return ["Go get the ball! Good dog!"]

    sentry_init(
        send_default_pii=True,
        traces_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )
    app = SentryWsgiMiddleware(
        dogpark,
        span_origin="auto.dogpark.deluxe",
    )

    if span_streaming:
        items = capture_items("span")
    else:
        events = capture_events()

    client = Client(app)
    client.get("/dogs/are/great/")

    sentry_sdk.flush()

    if span_streaming:
        assert len(items) == 1
        assert items[0].payload["attributes"]["sentry.origin"] == "auto.dogpark.deluxe"
    else:
        (event,) = events
        assert event["contexts"]["trace"]["origin"] == "auto.dogpark.deluxe"


@pytest.mark.parametrize(
    "has_file_wrapper, has_fileno, expect_wrapped",
    [
        (True, True, False),  # both conditions met → unwrapped
        (False, True, True),  # no file_wrapper → wrapped
        (True, False, True),  # no fileno → wrapped
        (False, False, True),  # neither condition → wrapped
    ],
)
def test_file_response_wrapping(
    sentry_init, has_file_wrapper, has_fileno, expect_wrapped
):
    sentry_init()

    response_mock = mock.MagicMock()
    if not has_fileno:
        del response_mock.fileno

    def app(environ, start_response):
        start_response("200 OK", [])
        return response_mock

    environ_extra = {}
    if has_file_wrapper:
        environ_extra["wsgi.file_wrapper"] = mock.MagicMock()

    middleware = SentryWsgiMiddleware(app)

    result = middleware(
        {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "80",
            "wsgi.url_scheme": "http",
            "wsgi.input": mock.MagicMock(),
            **environ_extra,
        },
        lambda status, headers: None,
    )

    if expect_wrapped:
        assert isinstance(result, _ScopedResponse)
    else:
        assert result is response_mock


@pytest.mark.parametrize(
    "environ,use_x_forwarded_for,expected_url",
    [
        # Without use_x_forwarded_for, wsgi.url_scheme is used
        (
            {
                "wsgi.url_scheme": "http",
                "SERVER_NAME": "example.com",
                "SERVER_PORT": "80",
                "PATH_INFO": "/test",
                "HTTP_X_FORWARDED_PROTO": "https",
            },
            False,
            "http://example.com/test",
        ),
        # With use_x_forwarded_for, HTTP_X_FORWARDED_PROTO is respected
        (
            {
                "wsgi.url_scheme": "http",
                "SERVER_NAME": "example.com",
                "SERVER_PORT": "80",
                "PATH_INFO": "/test",
                "HTTP_X_FORWARDED_PROTO": "https",
            },
            True,
            "https://example.com/test",
        ),
        # With use_x_forwarded_for but no forwarded proto, wsgi.url_scheme is used
        (
            {
                "wsgi.url_scheme": "http",
                "SERVER_NAME": "example.com",
                "SERVER_PORT": "80",
                "PATH_INFO": "/test",
            },
            True,
            "http://example.com/test",
        ),
        # Forwarded host with default https port is stripped using forwarded proto
        (
            {
                "wsgi.url_scheme": "http",
                "SERVER_NAME": "internal",
                "SERVER_PORT": "80",
                "PATH_INFO": "/test",
                "HTTP_X_FORWARDED_PROTO": "https",
                "HTTP_X_FORWARDED_HOST": "example.com:443",
            },
            True,
            "https://example.com/test",
        ),
        # Forwarded host with non-default port is preserved
        (
            {
                "wsgi.url_scheme": "http",
                "SERVER_NAME": "internal",
                "SERVER_PORT": "80",
                "PATH_INFO": "/test",
                "HTTP_X_FORWARDED_PROTO": "https",
                "HTTP_X_FORWARDED_HOST": "example.com:8443",
            },
            True,
            "https://example.com:8443/test",
        ),
        # Forwarded proto with HTTP_HOST (no forwarded host) strips default port
        (
            {
                "wsgi.url_scheme": "http",
                "HTTP_HOST": "example.com:443",
                "SERVER_NAME": "internal",
                "SERVER_PORT": "80",
                "PATH_INFO": "/test",
                "HTTP_X_FORWARDED_PROTO": "https",
            },
            True,
            "https://example.com/test",
        ),
    ],
    ids=[
        "ignores_forwarded_proto_when_disabled",
        "respects_forwarded_proto_when_enabled",
        "falls_back_to_url_scheme_when_no_forwarded_proto",
        "strips_default_https_port_from_forwarded_host",
        "preserves_non_default_port_on_forwarded_host",
        "strips_default_port_from_http_host_with_forwarded_proto",
    ],
)
def test_get_request_url_x_forwarded_proto(environ, use_x_forwarded_for, expected_url):
    assert get_request_url(environ, use_x_forwarded_for) == expected_url


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_request_headers_data_collection_default_redacts_sensitive(
    sentry_init, crashing_app, capture_events, send_default_pii
):
    """
    When ``data_collection`` is configured (even as ``None``, i.e. spec
    defaults), the WSGI event processor routes request headers through the
    data-collection filtering path. Sensitive headers are redacted regardless
    of ``send_default_pii`` -- the value of that legacy option must not change
    the outcome.
    """
    sentry_init(
        send_default_pii=send_default_pii,
        _experiments={"data_collection": None},
    )
    app = SentryWsgiMiddleware(crashing_app)
    client = Client(app)
    events = capture_events()

    with pytest.raises(ZeroDivisionError):
        client.get(
            "/",
            headers={
                "Authorization": "Bearer secret-token",
                "X-Custom-Header": "passthrough",
            },
        )

    (event,) = events
    headers = event["request"]["headers"]

    assert headers["Authorization"] == "[Filtered]"
    assert headers["X-Custom-Header"] == "passthrough"


def test_request_headers_legacy_no_pii_redacts_sensitive(
    sentry_init, crashing_app, capture_events
):
    """
    With no ``data_collection`` configured, ``_filter_headers`` falls back to
    the legacy ``send_default_pii`` behaviour. When PII is disabled, headers in
    ``SENSITIVE_HEADERS`` are replaced with an ``AnnotatedValue`` (the default
    ``use_annotated_value=True`` on the event-processor call site), which
    serializes to an emptied value plus a ``_meta`` annotation. Non-sensitive
    headers pass through untouched.

    ``X-Forwarded-For`` is used because it is in ``SENSITIVE_HEADERS`` but is
    not scrubbed by the default ``EventScrubber``, so the substitution we are
    asserting on can only come from ``_filter_headers``.
    """
    sentry_init(send_default_pii=False)
    app = SentryWsgiMiddleware(crashing_app)
    client = Client(app)
    events = capture_events()

    with pytest.raises(ZeroDivisionError):
        client.get(
            "/",
            headers={
                "X-Forwarded-For": "1.2.3.4",
                "X-Custom-Header": "passthrough",
            },
        )

    (event,) = events

    assert event["request"]["headers"]["X-Forwarded-For"] == ""
    assert event["request"]["headers"]["X-Custom-Header"] == "passthrough"

    # The emptied value is accompanied by a `_meta` annotation marking it as
    # removed, confirming the substitution came from the AnnotatedValue path.
    assert event["_meta"]["request"]["headers"]["X-Forwarded-For"] == {
        "": {"rem": [["!config", "x"]]}
    }


def test_request_headers_data_collection_off_collects_no_headers(
    sentry_init, crashing_app, capture_events
):
    """
    With ``http_headers.request`` mode set to ``off``, no request headers are
    collected at all -- the filtering returns an empty mapping.
    """
    sentry_init(
        _experiments={
            "data_collection": {"http_headers": {"request": {"mode": "off"}}}
        },
    )
    app = SentryWsgiMiddleware(crashing_app)
    client = Client(app)
    events = capture_events()

    with pytest.raises(ZeroDivisionError):
        client.get(
            "/",
            headers={
                "X-Forwarded-For": "1.2.3.4",
                "X-Custom-Header": "passthrough",
            },
        )

    (event,) = events

    assert event["request"]["headers"] == {}


def test_request_headers_data_collection_allowlist_redacts_all_but_allowed_terms(
    sentry_init, crashing_app, capture_events
):
    """
    An ``allowlist`` allows through only headers matching a configured term
    (partial, case-insensitive); every other header key is kept but its value
    is redacted.
    """
    sentry_init(
        _experiments={
            "data_collection": {
                "http_headers": {"request": {"mode": "allowlist", "terms": ["custom"]}}
            }
        },
    )
    app = SentryWsgiMiddleware(crashing_app)
    client = Client(app)
    events = capture_events()

    with pytest.raises(ZeroDivisionError):
        client.get(
            "/",
            headers={
                "X-Forwarded-For": "1.2.3.4",
                "X-Custom-Header": "passthrough",
            },
        )

    (event,) = events
    headers = event["request"]["headers"]

    assert headers["X-Custom-Header"] == "passthrough"
    assert headers["X-Forwarded-For"] == "[Filtered]"
    assert headers["Host"] == "[Filtered]"


def test_request_headers_data_collection_denylist_redacts_only_matched_terms(
    sentry_init, crashing_app, capture_events
):
    """
    A ``denylist`` passes headers through by default, redacting only those
    matching a configured term (partial, case-insensitive).
    """
    sentry_init(
        _experiments={
            "data_collection": {
                "http_headers": {"request": {"mode": "denylist", "terms": ["custom"]}}
            }
        },
    )
    app = SentryWsgiMiddleware(crashing_app)
    client = Client(app)
    events = capture_events()

    with pytest.raises(ZeroDivisionError):
        client.get(
            "/",
            headers={
                "X-Forwarded-For": "1.2.3.4",
                "X-Custom-Header": "passthrough",
            },
        )

    (event,) = events
    headers = event["request"]["headers"]

    assert headers["X-Custom-Header"] == "[Filtered]"
    assert headers["X-Forwarded-For"] == "1.2.3.4"
    assert headers["Host"] == "localhost"


def test_request_headers_data_collection_cookie_always_redacted(
    sentry_init, crashing_app, capture_events
):
    """
    The ``cookie``/``set-cookie`` headers are always redacted in the
    data-collection path, even when explicitly allowlisted. A sibling header
    (``custom``) that is also allowlisted passes through, isolating the
    cookie override.

    The middleware is driven with an explicit environ because werkzeug's test
    ``Client`` manages its own cookie jar and strips the ``Cookie`` header.
    """
    sentry_init(
        _experiments={
            "data_collection": {
                "http_headers": {
                    "request": {"mode": "allowlist", "terms": ["cookie", "custom"]}
                }
            }
        },
    )
    app = SentryWsgiMiddleware(crashing_app)
    events = capture_events()

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "wsgi.url_scheme": "http",
        "HTTP_COOKIE": "sessionid=secret",
        "HTTP_X_CUSTOM_HEADER": "passthrough",
    }

    with pytest.raises(ZeroDivisionError):
        list(app(environ, lambda status, headers: None))

    (event,) = events
    headers = event["request"]["headers"]

    assert headers["Cookie"] == "[Filtered]"
    assert headers["X-Custom-Header"] == "passthrough"


def test_request_headers_legacy_pii_passes_headers_through(
    sentry_init, crashing_app, capture_events
):
    """
    With no ``data_collection`` configured and ``send_default_pii`` enabled,
    the legacy path returns all headers unchanged -- including those in
    ``SENSITIVE_HEADERS``.
    """
    sentry_init(send_default_pii=True)
    app = SentryWsgiMiddleware(crashing_app)
    client = Client(app)
    events = capture_events()

    with pytest.raises(ZeroDivisionError):
        client.get(
            "/",
            headers={
                "X-Forwarded-For": "1.2.3.4",
                "X-Custom-Header": "passthrough",
            },
        )

    (event,) = events
    headers = event["request"]["headers"]

    assert headers["X-Forwarded-For"] == "1.2.3.4"
    assert headers["X-Custom-Header"] == "passthrough"


# Sentinel: the query string (event) / ``http.query`` attribute (span) is absent.
NO_QUERY_STRING = object()


@pytest.mark.parametrize(
    "init_kwargs, expected_query_string",
    [
        # No data_collection: the legacy path always sets the query string
        # unchanged, regardless of send_default_pii.
        pytest.param(
            {"send_default_pii": True},
            "toy=tennisball&color=red&auth=secret",
            id="send_default_pii_true",
        ),
        pytest.param(
            {"send_default_pii": False},
            "toy=tennisball&color=red&auth=secret",
            id="send_default_pii_false",
        ),
        pytest.param(
            {},
            "toy=tennisball&color=red&auth=secret",
            id="defaults",
        ),
        # data_collection configured: query string is routed through filtering.
        # Spec defaults -> denylist: only the sensitive ``auth`` is redacted.
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            "toy=tennisball&color=red&auth=[Filtered]",
            id="data_collection_denylist_default",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "denylist", "terms": ["toy"]}
                    }
                }
            },
            "toy=[Filtered]&color=red&auth=[Filtered]",
            id="data_collection_denylist_custom_terms",
        ),
        # allowlist with only ``toy`` allowed: ``color`` is redacted even though
        # it is not sensitive, proving the redaction comes from the allowlist.
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": ["toy"]}
                    }
                }
            },
            "toy=tennisball&color=[Filtered]&auth=[Filtered]",
            id="data_collection_allowlist",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                }
            },
            NO_QUERY_STRING,
            id="data_collection_off",
        ),
    ],
)
def test_query_string_data_collection(
    sentry_init, crashing_app, capture_events, init_kwargs, expected_query_string
):
    sentry_init(**init_kwargs)
    app = SentryWsgiMiddleware(crashing_app)
    client = Client(app)
    events = capture_events()

    with pytest.raises(ZeroDivisionError):
        client.get("/?toy=tennisball&color=red&auth=secret")

    (event,) = events

    if expected_query_string is NO_QUERY_STRING:
        assert "query_string" not in event["request"]
    else:
        assert event["request"]["query_string"] == expected_query_string


@pytest.mark.parametrize(
    "init_kwargs, expected_query",
    [
        # No data_collection: the ``http.query`` attribute follows the legacy
        # send_default_pii gate.
        pytest.param(
            {"send_default_pii": True},
            "toy=tennisball&color=red&auth=secret",
            id="send_default_pii_true",
        ),
        pytest.param(
            {"send_default_pii": False},
            NO_QUERY_STRING,
            id="send_default_pii_false",
        ),
        pytest.param(
            {},
            NO_QUERY_STRING,
            id="defaults",
        ),
        # data_collection configured: attribute is routed through filtering.
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            "toy=tennisball&color=red&auth=[Filtered]",
            id="data_collection_denylist_default",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "denylist", "terms": ["toy"]}
                    }
                }
            },
            "toy=[Filtered]&color=red&auth=[Filtered]",
            id="data_collection_denylist_custom_terms",
        ),
        # allowlist with only ``toy`` allowed: ``color`` is redacted even though
        # it is not sensitive, proving the redaction comes from the allowlist.
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": ["toy"]}
                    }
                }
            },
            "toy=tennisball&color=[Filtered]&auth=[Filtered]",
            id="data_collection_allowlist",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                }
            },
            NO_QUERY_STRING,
            id="data_collection_off",
        ),
    ],
)
def test_span_http_query_data_collection(
    sentry_init, capture_items, init_kwargs, expected_query
):
    def dogpark(environ, start_response):
        start_response("200 OK", [])
        return ["Go get the ball! Good dog!"]

    sentry_init(
        traces_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream",
            **init_kwargs.pop("_experiments", {}),
        },
        **init_kwargs,
    )
    app = SentryWsgiMiddleware(dogpark)
    client = Client(app)

    items = capture_items("span")

    client.get("/dogs/are/great?toy=tennisball&color=red&auth=secret")

    sentry_sdk.flush()

    (span,) = [item.payload for item in items]

    if expected_query is NO_QUERY_STRING:
        assert "http.query" not in span["attributes"]
    else:
        assert span["attributes"]["http.query"] == expected_query


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_user_ip_address_on_all_spans(sentry_init, capture_items, send_default_pii):
    def dogpark(environ, start_response):
        with sentry_sdk.traces.start_span(name="child-span"):
            pass
        start_response("200 OK", [])
        return ["Go get the ball! Good dog!"]

    sentry_init(
        send_default_pii=send_default_pii,
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    app = SentryWsgiMiddleware(dogpark)
    client = Client(app)

    items = capture_items("span")

    client.get("/dogs/are/great/", environ_base={"REMOTE_ADDR": "127.0.0.1"})

    sentry_sdk.flush()

    child_span, server_span = [item.payload for item in items]

    if send_default_pii:
        assert server_span["attributes"]["user.ip_address"] == "127.0.0.1"
        assert child_span["attributes"]["user.ip_address"] == "127.0.0.1"
    else:
        assert "user.ip_address" not in server_span["attributes"]
        assert "user.ip_address" not in child_span["attributes"]
