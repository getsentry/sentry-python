import pytest

from sentry_sdk.utils import sanitize_url


@pytest.mark.parametrize(
    ("url", "expected_result"),
    [
        ("http://localhost:8000", "http://localhost:8000"),
        ("http://example.com", "http://example.com"),
        ("https://example.com", "https://example.com"),
        (
            "example.com?token=abc&sessionid=123&save=true",
            "example.com?token=%s&sessionid=%s&save=%s",
        ),
        (
            "http://example.com?token=abc&sessionid=123&save=true",
            "http://example.com?token=%s&sessionid=%s&save=%s",
        ),
        (
            "https://example.com?token=abc&sessionid=123&save=true",
            "https://example.com?token=%s&sessionid=%s&save=%s",
        ),
        (
            "http://localhost:8000/?token=abc&sessionid=123&save=true",
            "http://localhost:8000/?token=%s&sessionid=%s&save=%s",
        ),
        (
            "ftp://username:password@ftp.example.com:9876/bla/blub#foo",
            "ftp://%s:%s@ftp.example.com:9876/bla/blub#foo",
        ),
        (
            "https://username:password@example.com/bla/blub?token=abc&sessionid=123&save=true#fragment",
            "https://%s:%s@example.com/bla/blub?token=%s&sessionid=%s&save=%s#fragment",
        ),
        (
            "http://example.com/bla?üsername=ada&pwd=häöüß",
            "http://example.com/bla?üsername=%s&pwd=%s",
        ),
        ("bla/blub/foo", "bla/blub/foo"),
        ("/bla/blub/foo/", "/bla/blub/foo/"),
        (
            "bla/blub/foo?token=abc&sessionid=123&save=true",
            "bla/blub/foo?token=%s&sessionid=%s&save=%s",
        ),
        (
            "/bla/blub/foo/?token=abc&sessionid=123&save=true",
            "/bla/blub/foo/?token=%s&sessionid=%s&save=%s",
        ),
    ],
)
def test_sanitize_url(url, expected_result):
    assert sanitize_url(url) == expected_result
