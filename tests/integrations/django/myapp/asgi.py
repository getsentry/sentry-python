"""
ASGI entrypoint. Configures Django and then runs the application
defined in the ASGI_APPLICATION setting.
"""

import os
import django
from channels.routing import get_default_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.integrations.django.myapp.settings")

django.setup()

from sentry_asgi import SentryMiddleware

application = get_default_application()
application = SentryMiddleware(application)
