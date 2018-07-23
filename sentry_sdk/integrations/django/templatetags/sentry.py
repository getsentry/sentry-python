from django.template import Library

from sentry_sdk import get_current_hub

register = Library()


@register.simple_tag
def sentry_dsn():
    if get_current_hub().client is not None:
        return get_current_hub().client.dsn or ""
    return ""
