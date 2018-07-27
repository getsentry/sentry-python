# Using Sentry with Django

In your ``settings.py``:

    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk import init

    init(dsn="https://foo@sentry.io/123", integrations=[DjangoIntegration()])

* All exceptions are reported.

* Request data is attached to your events.

* The current user ID, email and username from ``django.contrib.auth`` is
  attached to your events.

* Logging with any logger will create breadcrumbs. See logging docs for more
  information.
