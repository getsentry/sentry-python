"""
ASGI entrypoint. Configures Django and then runs the application
defined in the ASGI_APPLICATION setting.
"""

import os
import django
from channels.routing import get_default_application

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "tests.integrations.django.myapp.settings"
)

django.setup()
channels_application = get_default_application()

if django.VERSION >= (3, 0):
    from django.core.asgi import get_asgi_application

    asgi_application = get_asgi_application()
