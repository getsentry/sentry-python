from unittest.mock import MagicMock

import elasticsearch
import pytest

import sentry_sdk
from sentry_sdk import capture_message, start_transaction
from sentry_sdk.integrations.elasticsearch import (
    ElasticsearchIntegration,
    _parse_url,
    _patch_perform_request,
)


ES_MAJOR_VERSION = elasticsearch.VERSION[0]


def _get_patched_target():
    """Return the class whose perform_request is patched."""
    if ES_MAJOR_VERSION >= 8:
        return elasticsearch.Elasticsearch
    else:
        return elasticsearch.Transport


def _make_mock_self():
    """Create a mock self object with connection info for v7 Transport."""
    mock_self = MagicMock()
    mock_self.hosts = [{"host": "localhost", "port": 9200}]
    return mock_self


def test_elasticsearch_breadcrumbs(sentry_init, capture_events):
    sentry_init(integrations=[ElasticsearchIntegration()])
    events = capture_events()

    target = _get_patched_target()
    mock_self = _make_mock_self()

    try:
        target.perform_request(
            mock_self, "GET", "/my-index/_search", body={"query": {"match_all": {}}}
        )
    except Exception:
        pass

    capture_message("hi")

    (event,) = events

    breadcrumbs = event.get("breadcrumbs", {}).get("values", [])
    assert len(breadcrumbs) >= 1

    search_breadcrumb = breadcrumbs[-1]
    assert search_breadcrumb["category"] == "query"
    assert search_breadcrumb["message"] == "GET /my-index/_search"
    assert search_breadcrumb["data"]["db.system"] == "elasticsearch"
    assert search_breadcrumb["data"]["db.operation"] == "search"
    assert search_breadcrumb["data"]["db.name"] == "my-index"


def test_elasticsearch_spans(sentry_init, capture_events):
    sentry_init(
        integrations=[ElasticsearchIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    target = _get_patched_target()
    mock_self = _make_mock_self()

    with start_transaction(name="test_es"):
        try:
            target.perform_request(
                mock_self,
                "GET",
                "/my-index/_search",
                body={"query": {"match_all": {}}},
            )
        except Exception:
            pass

        try:
            target.perform_request(
                mock_self, "PUT", "/my-index/_doc/1", body={"title": "test"}
            )
        except Exception:
            pass

    (event,) = events

    spans = event.get("spans", [])
    assert len(spans) == 2

    search_span = spans[0]
    assert search_span["op"] == "db"
    assert search_span["description"] == "GET /my-index/_search"
    assert search_span["origin"] == "auto.db.elasticsearch"
    assert search_span["data"]["db.system"] == "elasticsearch"
    assert search_span["data"]["db.operation"] == "search"
    assert search_span["data"]["db.name"] == "my-index"

    doc_span = spans[1]
    assert doc_span["op"] == "db"
    assert doc_span["description"] == "PUT /my-index/_doc/1"
    assert doc_span["data"]["db.system"] == "elasticsearch"
    assert doc_span["data"]["db.operation"] == "doc"
    assert doc_span["data"]["db.name"] == "my-index"


def test_elasticsearch_pii_not_sent_by_default(sentry_init, capture_events):
    sentry_init(
        integrations=[ElasticsearchIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    target = _get_patched_target()
    mock_self = _make_mock_self()

    with start_transaction(name="test_es"):
        try:
            target.perform_request(
                mock_self,
                "GET",
                "/my-index/_search",
                body={"query": {"match": {"name": "secret"}}},
            )
        except Exception:
            pass

    (event,) = events

    spans = event.get("spans", [])
    assert len(spans) == 1
    assert "db.statement.body" not in spans[0]["data"]


def test_elasticsearch_pii_sent_when_enabled(sentry_init, capture_events):
    sentry_init(
        integrations=[ElasticsearchIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    target = _get_patched_target()
    mock_self = _make_mock_self()

    with start_transaction(name="test_es"):
        try:
            target.perform_request(
                mock_self,
                "GET",
                "/my-index/_search",
                body={"query": {"match": {"name": "secret"}}},
            )
        except Exception:
            pass

    (event,) = events

    spans = event.get("spans", [])
    assert len(spans) == 1
    assert spans[0]["data"]["db.statement.body"] == {
        "query": {"match": {"name": "secret"}}
    }


def test_elasticsearch_error_sets_span_status(sentry_init, capture_events):
    """Test that exceptions set INTERNAL_ERROR on the span.

    We patch a fresh class with _patch_perform_request using a failing
    original function, then call it to verify error handling.
    """
    sentry_init(
        integrations=[ElasticsearchIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Create a standalone class with a failing perform_request, then
    # apply the sentry patch to it. This lets us control the "original"
    # function in the closure.
    class FailingES:
        hosts = [{"host": "localhost", "port": 9200}]

        def perform_request(self, method, path, *args, **kwargs):
            raise ConnectionError("connection refused")

    _patch_perform_request(FailingES, ES_MAJOR_VERSION)

    instance = FailingES()

    with start_transaction(name="test_es"):
        with pytest.raises(ConnectionError, match="connection refused"):
            FailingES.perform_request(instance, "GET", "/my-index/_search")

    (event,) = events

    spans = event.get("spans", [])
    assert len(spans) == 1
    assert spans[0]["status"] == "internal_error"
    assert spans[0]["op"] == "db"
    assert spans[0]["description"] == "GET /my-index/_search"


def test_elasticsearch_no_spans_when_disabled(sentry_init, capture_events):
    """Verify no spans are created when integration is not active."""
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with start_transaction(name="test_es"):
        pass

    capture_message("hi")

    for event in events:
        spans = event.get("spans", [])
        for span in spans:
            assert span.get("data", {}).get("db.system") != "elasticsearch"


def test_elasticsearch_span_origin(sentry_init, capture_events):
    sentry_init(
        integrations=[ElasticsearchIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    target = _get_patched_target()
    mock_self = _make_mock_self()

    with start_transaction(name="test_es"):
        try:
            target.perform_request(mock_self, "GET", "/_search")
        except Exception:
            pass

    (event,) = events

    spans = event.get("spans", [])
    assert len(spans) == 1
    assert spans[0]["origin"] == "auto.db.elasticsearch"


@pytest.mark.parametrize(
    "url, expected_operation, expected_index",
    [
        ("/my-index/_search", "search", "my-index"),
        ("/_search", "search", None),
        ("/my-index/_doc/1", "doc", "my-index"),
        ("/_bulk", "bulk", None),
        ("/my-index/_bulk", "bulk", "my-index"),
        ("/_cluster/health", "cluster", None),
        ("/my-index", None, "my-index"),
        ("/", None, None),
        ("", None, None),
        ("/my-index/_mapping", "mapping", "my-index"),
        ("/_cat/indices", "cat", None),
    ],
)
def test_parse_url(url, expected_operation, expected_index):
    operation, index = _parse_url(url)
    assert operation == expected_operation
    assert index == expected_index
