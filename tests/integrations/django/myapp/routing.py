import channels

from channels.http import AsgiHandler
from channels.routing import ProtocolTypeRouter

if channels.__version__ < "3.0.0":
    channels_handler = AsgiHandler
else:
    channels_handler = AsgiHandler()

application = ProtocolTypeRouter({"http": channels_handler})
