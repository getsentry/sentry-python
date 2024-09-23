import channels
from channels.routing import ProtocolTypeRouter

try:
    from channels.http import AsgiHandler

    if channels.__version__ < "3.0.0":
        django_asgi_app = AsgiHandler
    else:
        django_asgi_app = AsgiHandler()

except ModuleNotFoundError:
    # Since channels 4.0 ASGI handling is done by Django itself
    from django.core.asgi import get_asgi_application

    django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter({"http": django_asgi_app})
