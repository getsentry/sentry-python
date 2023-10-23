from __future__ import absolute_import

import pytest
import django
from werkzeug.test import Client

# django<2.0 had only `url` with regex based patterns.
# django>=2.0 renamed `url` to `re_path`, and additionally introduced `path`
# for new style URL patterns, e.g. <article_id:int>.
if django.VERSION >= (2, 0):
    from django.urls import path, re_path
    from django.conf.urls import include
    from django.urls.converters import PathConverter
else:
    from django.conf.urls import url as re_path, include

from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.django.transactions import RavenResolver
from tests.integrations.django.myapp.wsgi import application


@pytest.fixture
def client():
    return Client(application)


if django.VERSION < (1, 9):
    included_url_conf = (re_path(r"^foo/bar/(?P<param>[\w]+)", lambda x: ""),), "", ""
else:
    included_url_conf = ((re_path(r"^foo/bar/(?P<param>[\w]+)", lambda x: ""),), "")

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


def test_resolver_no_match():
    resolver = RavenResolver()
    result = resolver.resolve("/foo/bar", example_url_conf)
    assert result is None


def test_resolver_complex_match():
    resolver = RavenResolver()
    result = resolver.resolve("/api/1234/store/", example_url_conf)
    assert result == "/api/{project_id}/store/"


def test_resolver_complex_either_match():
    resolver = RavenResolver()
    result = resolver.resolve("/api/v1/author/", example_url_conf)
    assert result == "/api/{version}/author/"
    result = resolver.resolve("/api/v2/author/", example_url_conf)
    assert result == "/api/{version}/author/"


def test_resolver_included_match():
    resolver = RavenResolver()
    result = resolver.resolve("/example/foo/bar/baz", example_url_conf)
    assert result == "/example/foo/bar/{param}"


def test_resolver_multiple_groups():
    resolver = RavenResolver()
    result = resolver.resolve(
        "/api/myproject/product/cb4ef1caf3554c34ae134f3c1b3d605f/", example_url_conf
    )
    assert result == "/api/{project_id}/product/{pid}/"


@pytest.mark.skipif(django.VERSION < (2, 0), reason="Requires Django > 2.0")
def test_resolver_new_style_group():
    url_conf = (path("api/v2/<int:project_id>/store/", lambda x: ""),)
    resolver = RavenResolver()
    result = resolver.resolve("/api/v2/1234/store/", url_conf)
    assert result == "/api/v2/{project_id}/store/"


@pytest.mark.skipif(django.VERSION < (2, 0), reason="Requires Django > 2.0")
def test_resolver_new_style_multiple_groups():
    url_conf = (path("api/v2/<int:project_id>/product/<int:pid>", lambda x: ""),)
    resolver = RavenResolver()
    result = resolver.resolve("/api/v2/1234/product/5689", url_conf)
    assert result == "/api/v2/{project_id}/product/{pid}"
