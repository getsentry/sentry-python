from __future__ import absolute_import

import django

if django.VERSION >= (2, 0):
    # TODO: once we stop supporting django < 2, use the real name of this
    # function (re_path)
    from django.urls import re_path as url
else:
    from django.conf.urls import url


urlpatterns = [
    url(r"^foo/bar/baz/(?P<param>[\d]+)", lambda x: ""),
]
