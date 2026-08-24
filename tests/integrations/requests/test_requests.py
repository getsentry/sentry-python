import sys
from unittest import mock

import pytest
import requests

from sentry_sdk import capture_message
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.stdlib import StdlibIntegration
from tests.conftest import ApproxDict, create_mock_http_server

PORT = create_mock_http_server()


def test_crumb_capture(sentry_init, capture_events):
    sentry_init(integrations=[StdlibIntegration()], send_default_pii=True)
    events = capture_events()

    url = f"http://localhost:{PORT}/hello-world"  # noqa:E231
    response = requests.get(url)
    capture_message("Testing!")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]
    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"
    assert crumb["data"] == ApproxDict(
        {
            "url": url,
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_FRAGMENT: "",
            SPANDATA.HTTP_QUERY: "",
            SPANDATA.HTTP_STATUS_CODE: response.status_code,
            "reason": response.reason,
        }
    )


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_crumb_capture_span_streaming(sentry_init, capture_events, send_default_pii):
    sentry_init(
        integrations=[StdlibIntegration()],
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )
    events = capture_events()

    url = f"http://localhost:{PORT}/hello-world"  # noqa:E231
    response = requests.get(url)
    capture_message("Testing!")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]
    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"

    if send_default_pii:
        assert crumb["data"] == ApproxDict(
            {
                SPANDATA.URL_FULL: url,
                SPANDATA.HTTP_REQUEST_METHOD: "GET",
                SPANDATA.URL_FRAGMENT: "",
                SPANDATA.URL_QUERY: "",
                SPANDATA.HTTP_STATUS_CODE: response.status_code,
            }
        )
    else:
        assert crumb["data"] == ApproxDict(
            {
                SPANDATA.HTTP_REQUEST_METHOD: "GET",
                SPANDATA.HTTP_STATUS_CODE: response.status_code,
            }
        )


@pytest.mark.skipif(
    sys.version_info < (3, 7),
    reason="The response status is not set on the span early enough in 3.6",
)
@pytest.mark.parametrize(
    "status_code,level",
    [
        (200, None),
        (301, None),
        (403, "warning"),
        (405, "warning"),
        (500, "error"),
    ],
)
def test_crumb_capture_client_error(sentry_init, capture_events, status_code, level):
    sentry_init(integrations=[StdlibIntegration()], send_default_pii=True)

    events = capture_events()

    url = f"http://localhost:{PORT}/status/{status_code}"  # noqa:E231
    response = requests.get(url)

    assert response.status_code == status_code

    capture_message("Testing!")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]
    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"

    if level is None:
        assert "level" not in crumb
    else:
        assert crumb["level"] == level

    assert crumb["data"] == ApproxDict(
        {
            "url": url,
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_FRAGMENT: "",
            SPANDATA.HTTP_QUERY: "",
            SPANDATA.HTTP_STATUS_CODE: response.status_code,
            "reason": response.reason,
        }
    )


@pytest.mark.skipif(
    sys.version_info < (3, 7),
    reason="The response status is not set on the span early enough in 3.6",
)
@pytest.mark.parametrize(
    "status_code,level",
    [
        (200, None),
        (301, None),
        (403, "warning"),
        (405, "warning"),
        (500, "error"),
    ],
)
@pytest.mark.parametrize("send_default_pii", [True, False])
def test_crumb_capture_client_error_span_streaming(
    sentry_init, capture_events, status_code, level, send_default_pii
):
    sentry_init(
        integrations=[StdlibIntegration()],
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    events = capture_events()

    url = f"http://localhost:{PORT}/status/{status_code}"  # noqa:E231
    response = requests.get(url)

    assert response.status_code == status_code

    capture_message("Testing!")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]
    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"

    if level is None:
        assert "level" not in crumb
    else:
        assert crumb["level"] == level

    if send_default_pii:
        assert crumb["data"] == ApproxDict(
            {
                SPANDATA.URL_FULL: url,
                SPANDATA.HTTP_REQUEST_METHOD: "GET",
                SPANDATA.URL_FRAGMENT: "",
                SPANDATA.URL_QUERY: "",
                SPANDATA.HTTP_STATUS_CODE: response.status_code,
            }
        )
    else:
        assert crumb["data"] == ApproxDict(
            {
                SPANDATA.HTTP_REQUEST_METHOD: "GET",
                SPANDATA.HTTP_STATUS_CODE: response.status_code,
            }
        )


@pytest.mark.tests_internal_exceptions
def test_omit_url_data_if_parsing_fails(sentry_init, capture_events):
    sentry_init(integrations=[StdlibIntegration()])

    events = capture_events()

    url = f"http://localhost:{PORT}/ok"  # noqa:E231

    with mock.patch(
        "sentry_sdk.integrations.stdlib.parse_url",
        side_effect=ValueError,
    ):
        response = requests.get(url)

    capture_message("Testing!")

    (event,) = events
    assert event["breadcrumbs"]["values"][0]["data"] == ApproxDict(
        {
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_STATUS_CODE: response.status_code,
            "reason": response.reason,
            # no url related data
        }
    )
    assert "url" not in event["breadcrumbs"]["values"][0]["data"]
    assert SPANDATA.HTTP_FRAGMENT not in event["breadcrumbs"]["values"][0]["data"]
    assert SPANDATA.HTTP_QUERY not in event["breadcrumbs"]["values"][0]["data"]


@pytest.mark.tests_internal_exceptions
def test_omit_url_data_if_parsing_fails_span_streaming(sentry_init, capture_events):
    sentry_init(
        integrations=[StdlibIntegration()],
        trace_lifecycle="stream",
        send_default_pii=True,
    )

    events = capture_events()

    url = f"http://localhost:{PORT}/ok"  # noqa:E231

    with mock.patch(
        "sentry_sdk.integrations.stdlib.parse_url",
        side_effect=ValueError,
    ):
        response = requests.get(url)

    capture_message("Testing!")

    (event,) = events
    assert event["breadcrumbs"]["values"][0]["data"] == ApproxDict(
        {
            SPANDATA.HTTP_REQUEST_METHOD: "GET",
            SPANDATA.HTTP_STATUS_CODE: response.status_code,
            # no url related data
        }
    )
    assert SPANDATA.URL_FULL not in event["breadcrumbs"]["values"][0]["data"]
    assert SPANDATA.URL_FRAGMENT not in event["breadcrumbs"]["values"][0]["data"]
    assert SPANDATA.URL_QUERY not in event["breadcrumbs"]["values"][0]["data"]
