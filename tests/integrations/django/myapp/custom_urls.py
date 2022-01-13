from __future__ import absolute_import

try:
    from django.urls import path
except ImportError:
    from django.conf.urls import url

    def path(path, *args, **kwargs):
        return url("^{}$".format(path), *args, **kwargs)


from . import views

urlpatterns = [
    path(
        "foo/bar/baz/<int:param>/",
        views.postgres_select_custom,
        name="postgres_select_custom",
    ),
]
