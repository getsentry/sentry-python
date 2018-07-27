# Using Sentry with Celery

Just add ``CeleryIntegration()`` to your ``integrations`` array. For example, in Django:

    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk import init

    init(dsn="https://foo@sentry.io/123", integrations=[DjangoIntegration(), CeleryIntegration()])


With that, the transaction on the event will be set to the task name, and
exceptions will be reported to Sentry.
