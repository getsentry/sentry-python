from unittest.mock import MagicMock

import pytest
from opentelemetry.trace import SpanKind, Status, StatusCode

from sentry_sdk.integrations.opentelemetry.utils import (
    extract_span_data,
    extract_span_status,
    span_data_for_db_query,
    span_data_for_http_method,
)


@pytest.mark.parametrize(
    "name, status, attributes, expected",
    [
        (
            "OTel Span Blank",
            Status(StatusCode.UNSET),  # Unset defaults to OK
            {},
            {
                "op": "OTel Span Blank",
                "description": "OTel Span Blank",
                "status": "ok",
                "http_status_code": None,
            },
        ),
        (
            "OTel Span RPC",
            Status(StatusCode.UNSET),  # Unset defaults to OK
            {
                "rpc.service": "myservice.EchoService",
            },
            {
                "op": "rpc",
                "description": "OTel Span RPC",
                "status": "ok",
                "http_status_code": None,
            },
        ),
        (
            "OTel Span Messaging",
            Status(StatusCode.UNSET),  # Unset defaults to OK
            {
                "messaging.system": "rabbitmq",
            },
            {
                "op": "message",
                "description": "OTel Span Messaging",
                "status": "ok",
                "http_status_code": None,
            },
        ),
        (
            "OTel Span FaaS",
            Status(StatusCode.UNSET),  # Unset defaults to OK
            {
                "faas.trigger": "pubsub",
            },
            {
                "op": "pubsub",
                "description": "OTel Span FaaS",
                "status": "ok",
                "http_status_code": None,
            },
        ),
    ],
)
def test_extract_span_data(name, status, attributes, expected):
    otel_span = MagicMock()
    otel_span.name = name
    otel_span.status = Status(StatusCode.UNSET)
    otel_span.attributes = attributes

    op, description, status, http_status_code = extract_span_data(otel_span)
    result = {
        "op": op,
        "description": description,
        "status": status,
        "http_status_code": http_status_code,
    }
    assert result == expected


@pytest.mark.parametrize(
    "kind, status, attributes, expected",
    [
        (
            SpanKind.CLIENT,
            Status(StatusCode.OK),
            {
                "http.method": "GET",
                "http.target": None,  # no location for description
                "net.peer.name": None,
                "http.url": None,
            },
            {
                "op": "http.client",
                "description": "GET",
                "status": "ok",
                "http_status_code": None,
            },
        ),
        (
            SpanKind.CLIENT,
            Status(StatusCode.OK),
            {
                "http.method": "GET",
                "http.target": "/target",  # this can be the location in the description
            },
            {
                "op": "http.client",
                "description": "GET /target",
                "status": "ok",
                "http_status_code": None,
            },
        ),
        (
            SpanKind.CLIENT,
            Status(StatusCode.OK),
            {
                "http.method": "GET",
                "net.peer.name": "example.com",  # this can be the location in the description
            },
            {
                "op": "http.client",
                "description": "GET example.com",
                "status": "ok",
                "http_status_code": None,
            },
        ),
        (
            SpanKind.CLIENT,
            Status(StatusCode.OK),
            {
                "http.method": "GET",
                "http.target": "/target",  # target takes precedence over net.peer.name
                "net.peer.name": "example.com",
            },
            {
                "op": "http.client",
                "description": "GET /target",
                "status": "ok",
                "http_status_code": None,
            },
        ),
        (
            SpanKind.CLIENT,
            Status(StatusCode.OK),
            {
                "http.method": "GET",
                "http.url": "https://username:secretpwd@example.com/bla/?secret=123&anothersecret=456",  # sensitive data is stripped
            },
            {
                "op": "http.client",
                "description": "GET https://example.com/bla/",
                "status": "ok",
                "http_status_code": None,
            },
        ),
    ],
)
def test_span_data_for_http_method(kind, status, attributes, expected):
    otel_span = MagicMock()
    otel_span.kind = kind
    otel_span.status = status
    otel_span.attributes = attributes

    op, description, status, http_status_code = span_data_for_http_method(otel_span)
    result = {
        "op": op,
        "description": description,
        "status": status,
        "http_status_code": http_status_code,
    }
    assert result == expected


def test_span_data_for_db_query():
    otel_span = MagicMock()
    otel_span.name = "OTel Span"
    otel_span.attributes = {}

    op, description, status, http_status = span_data_for_db_query(otel_span)
    assert op == "db"
    assert description == "OTel Span"
    assert status is None
    assert http_status is None

    otel_span.attributes = {"db.statement": "SELECT * FROM table;"}

    op, description, status, http_status = span_data_for_db_query(otel_span)
    assert op == "db"
    assert description == "SELECT * FROM table;"
    assert status is None
    assert http_status is None


@pytest.mark.parametrize(
    "kind, status, attributes, expected",
    [
        (
            SpanKind.CLIENT,
            None,  # None means unknown error
            {
                "http.method": "POST",
                "http.route": "/some/route",
            },
            {
                "status": "unknown_error",
                "http_status_code": None,
            },
        ),
        (
            SpanKind.CLIENT,
            None,
            {
                "http.method": "POST",
                "http.route": "/some/route",
                "http.status_code": 502,  # Take this status in case of None status
            },
            {
                "status": "internal_error",
                "http_status_code": 502,
            },
        ),
        (
            SpanKind.SERVER,
            Status(StatusCode.UNSET),  # Unset defaults to OK
            {
                "http.method": "POST",
                "http.route": "/some/route",
            },
            {
                "status": "ok",
                "http_status_code": None,
            },
        ),
        (
            SpanKind.SERVER,
            Status(StatusCode.UNSET),
            {
                "http.method": "POST",
                "http.route": "/some/route",
                "http.status_code": 502,  # Take this status in case of UNSET status
            },
            {
                "status": "internal_error",
                "http_status_code": 502,
            },
        ),
        (
            SpanKind.SERVER,
            None,
            {
                "http.method": "POST",
                "http.route": "/some/route",
                "http.status_code": 502,
                "http.response.status_code": 503,  # this takes precedence over deprecated http.status_code
            },
            {
                "status": "unavailable",
                "http_status_code": 503,
            },
        ),
        (
            SpanKind.SERVER,
            Status(StatusCode.UNSET),
            {
                "http.method": "POST",
                "http.route": "/some/route",
                "http.status_code": 502,
                "http.response.status_code": 503,  # this takes precedence over deprecated http.status_code
            },
            {
                "status": "unavailable",
                "http_status_code": 503,
            },
        ),
        (
            SpanKind.SERVER,
            Status(StatusCode.OK),  # OK status is taken right away
            {
                "http.method": "POST",
                "http.route": "/some/route",
            },
            {
                "status": "ok",
                "http_status_code": None,
            },
        ),
        (
            SpanKind.SERVER,
            Status(StatusCode.OK),  # OK status is taken right away
            {
                "http.method": "POST",
                "http.route": "/some/route",
                "http.response.status_code": 200,
            },
            {
                "status": "ok",
                "http_status_code": 200,
            },
        ),
        (
            SpanKind.SERVER,
            Status(
                StatusCode.ERROR
            ),  # Error status without description gets the http status from attributes
            {
                "http.method": "POST",
                "http.route": "/some/route",
                "http.response.status_code": 401,
            },
            {
                "status": "unauthenticated",
                "http_status_code": 401,
            },
        ),
        (
            SpanKind.SERVER,
            Status(
                StatusCode.ERROR, "I'm a teapot"
            ),  # Error status with unknown description is an unknown error
            {
                "http.method": "POST",
                "http.route": "/some/route",
                "http.response.status_code": 418,
            },
            {
                "status": "unknown_error",
                "http_status_code": None,
            },
        ),
        (
            SpanKind.SERVER,
            Status(
                StatusCode.ERROR, "unimplemented"
            ),  # Error status with known description is taken (grpc errors)
            {
                "http.method": "POST",
                "http.route": "/some/route",
            },
            {
                "status": "unimplemented",
                "http_status_code": None,
            },
        ),
    ],
)
def test_extract_span_status(kind, status, attributes, expected):
    otel_span = MagicMock()
    otel_span.kind = kind
    otel_span.status = status
    otel_span.attributes = attributes

    status, http_status_code = extract_span_status(otel_span)
    result = {
        "status": status,
        "http_status_code": http_status_code,
    }
    assert result == expected
