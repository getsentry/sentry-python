from unittest import mock

import pytest
import django
from django.utils.translation import pgettext_lazy


# django<2.0 has only `url` with regex based patterns.
# django>=2.0 renames `url` to `re_path`, and additionally introduces `path`
# for new style URL patterns, e.g. <int:article_id>.
if django.VERSION >= (2, 0):
    from django.urls import path, re_path
    from django.urls.converters import PathConverter
    from django.conf.urls import include
else:
    from django.conf.urls import url as re_path, include

if django.VERSION < (1, 9):
    included_url_conf = (re_path(r"^foo/bar/(?P<param>[\w]+)", lambda x: ""),), "", ""
else:
    included_url_conf = ((re_path(r"^foo/bar/(?P<param>[\w]+)", lambda x: ""),), "")

from sentry_sdk.integrations.django.transactions import RavenResolver


example_url_conf = (
    re_path(r"^api/(?P<project_id>[\w_-]+)/store/$", lambda x: ""),
    re_path(r"^api/(?P<version>(v1|v2))/author/$", lambda x: ""),
    re_path(
        r"^api/(?P<project_id>[^\/]+)/product/(?P<pid>(?:\d+|[A-Fa-f0-9-]{32,36}))/$",
        lambda x: "",
    ),
    re_path(r"^report/", lambda x: ""),
    re_path(r"^example/", include(included_url_conf)),
)


@pytest.mark.skip
def test_resolver_no_match():
    resolver = RavenResolver()
    result = resolver.resolve("/foo/bar", example_url_conf)
    assert result is None


@pytest.mark.skip
def test_resolver_re_path_complex_match():
    resolver = RavenResolver()
    result = resolver.resolve("/api/1234/store/", example_url_conf)
    assert result == "/api/{project_id}/store/"


@pytest.mark.skip
def test_resolver_re_path_complex_either_match():
    resolver = RavenResolver()
    result = resolver.resolve("/api/v1/author/", example_url_conf)
    assert result == "/api/{version}/author/"
    result = resolver.resolve("/api/v2/author/", example_url_conf)
    assert result == "/api/{version}/author/"


@pytest.mark.skip
def test_resolver_re_path_included_match():
    resolver = RavenResolver()
    result = resolver.resolve("/example/foo/bar/baz", example_url_conf)
    assert result == "/example/foo/bar/{param}"


@pytest.mark.skip
def test_resolver_re_path_multiple_groups():
    resolver = RavenResolver()
    result = resolver.resolve(
        "/api/myproject/product/cb4ef1caf3554c34ae134f3c1b3d605f/", example_url_conf
    )
    assert result == "/api/{project_id}/product/{pid}/"


@pytest.mark.skipif(
    django.VERSION < (2, 0),
    reason="Django>=2.0 required for <converter:parameter> patterns",
)
@pytest.mark.skip
def test_resolver_path_group():
    url_conf = (path("api/v2/<int:project_id>/store/", lambda x: ""),)
    resolver = RavenResolver()
    result = resolver.resolve("/api/v2/1234/store/", url_conf)
    assert result == "/api/v2/{project_id}/store/"


@pytest.mark.skipif(
    django.VERSION < (2, 0),
    reason="Django>=2.0 required for <converter:parameter> patterns",
)
@pytest.mark.skip
def test_resolver_path_multiple_groups():
    url_conf = (path("api/v2/<str:project_id>/product/<int:pid>", lambda x: ""),)
    resolver = RavenResolver()
    result = resolver.resolve("/api/v2/myproject/product/5689", url_conf)
    assert result == "/api/v2/{project_id}/product/{pid}"


@pytest.mark.skipif(
    django.VERSION < (2, 0),
    reason="Django>=2.0 required for <converter:parameter> patterns",
)
@pytest.mark.skipif(
    django.VERSION > (5, 1),
    reason="get_converter removed in 5.1",
)
@pytest.mark.skip
def test_resolver_path_complex_path_legacy():
    class CustomPathConverter(PathConverter):
        regex = r"[^/]+(/[^/]+){0,2}"

    with mock.patch(
        "django.urls.resolvers.get_converter",
        return_value=CustomPathConverter,
    ):
        url_conf = (path("api/v3/<custom_path:my_path>", lambda x: ""),)
        resolver = RavenResolver()
        result = resolver.resolve("/api/v3/abc/def/ghi", url_conf)
        assert result == "/api/v3/{my_path}"


@pytest.mark.skipif(
    django.VERSION < (5, 1),
    reason="get_converters is used in 5.1",
)
@pytest.mark.skip
def test_resolver_path_complex_path():
    class CustomPathConverter(PathConverter):
        regex = r"[^/]+(/[^/]+){0,2}"

    with mock.patch(
        "django.urls.resolvers.get_converters",
        return_value={"custom_path": CustomPathConverter},
    ):
        url_conf = (path("api/v3/<custom_path:my_path>", lambda x: ""),)
        resolver = RavenResolver()
        result = resolver.resolve("/api/v3/abc/def/ghi", url_conf)
        assert result == "/api/v3/{my_path}"


@pytest.mark.skipif(
    django.VERSION < (2, 0),
    reason="Django>=2.0 required for <converter:parameter> patterns",
)
@pytest.mark.skip
def test_resolver_path_no_converter():
    url_conf = (path("api/v4/<project_id>", lambda x: ""),)
    resolver = RavenResolver()
    result = resolver.resolve("/api/v4/myproject", url_conf)
    assert result == "/api/v4/{project_id}"


@pytest.mark.skipif(
    django.VERSION < (2, 0),
    reason="Django>=2.0 required for path patterns",
)
@pytest.mark.skip
def test_resolver_path_with_i18n():
    url_conf = (path(pgettext_lazy("url", "pgettext"), lambda x: ""),)
    resolver = RavenResolver()
    result = resolver.resolve("/pgettext", url_conf)
    assert result == "/pgettext"
