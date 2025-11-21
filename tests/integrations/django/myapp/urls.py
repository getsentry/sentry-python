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

try:
    from django.urls import path
except ImportError:
    from django.conf.urls import url

    def path(path, *args, **kwargs):
        return url("^{}$".format(path), *args, **kwargs)


from . import views
from django_helpers import views as helper_views

urlpatterns = [
    path("view-exc", views.view_exc, name="view_exc"),
    path("view-exc-with-msg", views.view_exc_with_msg, name="view_exc_with_msg"),
    path("cached-view", views.cached_view, name="cached_view"),
    path("not-cached-view", views.not_cached_view, name="not_cached_view"),
    path(
        "view-with-cached-template-fragment",
        views.view_with_cached_template_fragment,
        name="view_with_cached_template_fragment",
    ),
    path(
        "read-body-and-view-exc",
        views.read_body_and_view_exc,
        name="read_body_and_view_exc",
    ),
    path("middleware-exc", views.message, name="middleware_exc"),
    path("message", views.message, name="message"),
    path("nomessage", views.nomessage, name="nomessage"),
    path("view-with-signal", views.view_with_signal, name="view_with_signal"),
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
    path("template-test3", views.template_test3, name="template_test3"),
    path("template-test4", views.template_test4, name="template_test4"),
    path("postgres-select", views.postgres_select, name="postgres_select"),
    path("postgres-select-slow", views.postgres_select_orm, name="postgres_select_orm"),
    path(
        "postgres-insert-no-autocommit",
        views.postgres_insert_orm_no_autocommit,
        name="postgres_insert_orm_no_autocommit",
    ),
    path(
        "postgres-insert-no-autocommit-rollback",
        views.postgres_insert_orm_no_autocommit_rollback,
        name="postgres_insert_orm_no_autocommit_rollback",
    ),
    path(
        "postgres-insert-atomic",
        views.postgres_insert_orm_atomic,
        name="postgres_insert_orm_atomic",
    ),
    path(
        "postgres-insert-atomic-rollback",
        views.postgres_insert_orm_atomic_rollback,
        name="postgres_insert_orm_atomic_rollback",
    ),
    path(
        "postgres-insert-atomic-exception",
        views.postgres_insert_orm_atomic_exception,
        name="postgres_insert_orm_atomic_exception",
    ),
    path(
        "postgres-select-slow-from-supplement",
        helper_views.postgres_select_orm,
        name="postgres_select_slow_from_supplement",
    ),
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
    path("sync/thread_ids", views.thread_ids_sync, name="thread_ids_sync"),
    path(
        "send-myapp-custom-signal",
        views.send_myapp_custom_signal,
        name="send_myapp_custom_signal",
    ),
]

# async views
if views.async_message is not None:
    urlpatterns.append(path("async_message", views.async_message, name="async_message"))

if views.my_async_view is not None:
    urlpatterns.append(path("my_async_view", views.my_async_view, name="my_async_view"))

if views.my_async_view is not None:
    urlpatterns.append(
        path("simple_async_view", views.simple_async_view, name="simple_async_view")
    )

if views.thread_ids_async is not None:
    urlpatterns.append(
        path("async/thread_ids", views.thread_ids_async, name="thread_ids_async")
    )

if views.post_echo_async is not None:
    urlpatterns.append(
        path("post_echo_async", views.post_echo_async, name="post_echo_async")
    )

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
