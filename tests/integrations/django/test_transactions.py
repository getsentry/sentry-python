from __future__ import absolute_import

import pytest
import django

if django.VERSION >= (2, 0):
    # TODO: once we stop supporting django < 2, use the real name of this
    # function (re_path)
    from django.urls import re_path as url
    from django.conf.urls import include
else:
    from django.conf.urls import url, include

if django.VERSION < (1, 9):
    included_url_conf = (url(r"^foo/bar/(?P<param>[\w]+)", lambda x: ""),), "", ""
else:
    included_url_conf = ((url(r"^foo/bar/(?P<param>[\w]+)", lambda x: ""),), "")

from sentry_sdk.integrations.django.transactions import RavenResolver


example_url_conf = (
    url(r"^api/(?P<project_id>[\w_-]+)/store/$", lambda x: ""),
    url(r"^api/(?P<version>(v1|v2))/author/$", lambda x: ""),
    url(r"^report/", lambda x: ""),
    url(r"^example/", include(included_url_conf)),
)


def test_legacy_resolver_no_match():
    resolver = RavenResolver()
    result = resolver.resolve("/foo/bar", example_url_conf)
    assert result == "/foo/bar"


def test_legacy_resolver_complex_match():
    resolver = RavenResolver()
    result = resolver.resolve("/api/1234/store/", example_url_conf)
    assert result == "/api/{project_id}/store/"


def test_legacy_resolver_complex_either_match():
    resolver = RavenResolver()
    result = resolver.resolve("/api/v1/author/", example_url_conf)
    assert result == "/api/{version}/author/"
    result = resolver.resolve("/api/v2/author/", example_url_conf)
    assert result == "/api/{version}/author/"


def test_legacy_resolver_included_match():
    resolver = RavenResolver()
    result = resolver.resolve("/example/foo/bar/baz", example_url_conf)
    assert result == "/example/foo/bar/{param}"


@pytest.mark.skipif(django.VERSION < (2, 0), reason="Requires Django > 2.0")
def test_legacy_resolver_newstyle_django20_urlconf():
    from django.urls import path

    url_conf = (path("api/v2/<int:project_id>/store/", lambda x: ""),)
    resolver = RavenResolver()
    result = resolver.resolve("/api/v2/1234/store/", url_conf)
    assert result == "/api/v2/{project_id}/store/"


def test_legacy_resolver_custom_urlconf_module_name():
    resolver = RavenResolver()
    custom_urlconf_module = "tests.integrations.django.myapp.custom_urls"
    result = resolver.resolve("/foo/bar/baz/1234/", custom_urlconf_module)
    assert result == "/foo/bar/baz/{param}/"


@pytest.mark.only
def test_legacy_resolver_custom_urlconf_callback():
    def custom_urlconf_callback():
        return "tests.integrations.django.myapp.custom_urls"

    resolver = RavenResolver()
    result = resolver.resolve("/foo/bar/baz/1234/", custom_urlconf_callback)
    assert result == "/foo/bar/baz/{param}/"
