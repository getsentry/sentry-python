"""myapp URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from __future__ import absolute_import

try:
    from django.urls import path
except ImportError:
    from django.conf.urls import url

    def path(path, *args, **kwargs):
        return url("^{}$".format(path), *args, **kwargs)


from . import views

urlpatterns = [
    path("view-exc", views.view_exc, name="view_exc"),
    path(
        "read-body-and-view-exc",
        views.read_body_and_view_exc,
        name="read_body_and_view_exc",
    ),
    path("middleware-exc", views.message, name="middleware_exc"),
    path("message", views.message, name="message"),
    path("mylogin", views.mylogin, name="mylogin"),
    path("classbased", views.ClassBasedView.as_view(), name="classbased"),
    path("sentryclass", views.SentryClassBasedView(), name="sentryclass"),
    path(
        "sentryclass-csrf",
        views.SentryClassBasedViewWithCsrf(),
        name="sentryclass_csrf",
    ),
    path("post-echo", views.post_echo, name="post_echo"),
    path("template-exc", views.template_exc, name="template_exc"),
    path("template-test", views.template_test, name="template_test"),
    path("template-test2", views.template_test2, name="template_test2"),
    path("postgres-select", views.postgres_select, name="postgres_select"),
    path(
        "permission-denied-exc",
        views.permission_denied_exc,
        name="permission_denied_exc",
    ),
    path(
        "csrf-hello-not-exempt",
        views.csrf_hello_not_exempt,
        name="csrf_hello_not_exempt",
    ),
]

# async views
if views.async_message is not None:
    urlpatterns.append(path("async_message", views.async_message, name="async_message"))

if views.my_async_view is not None:
    urlpatterns.append(path("my_async_view", views.my_async_view, name="my_async_view"))

# rest framework
try:
    urlpatterns.append(
        path("rest-framework-exc", views.rest_framework_exc, name="rest_framework_exc")
    )
    urlpatterns.append(
        path(
            "rest-framework-read-body-and-exc",
            views.rest_framework_read_body_and_exc,
            name="rest_framework_read_body_and_exc",
        )
    )
    urlpatterns.append(path("rest-hello", views.rest_hello, name="rest_hello"))
    urlpatterns.append(
        path("rest-json-response", views.rest_json_response, name="rest_json_response")
    )
    urlpatterns.append(
        path(
            "rest-permission-denied-exc",
            views.rest_permission_denied_exc,
            name="rest_permission_denied_exc",
        )
    )
except AttributeError:
    pass

handler500 = views.handler500
handler404 = views.handler404
