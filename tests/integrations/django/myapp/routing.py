from channels.http import AsgiHandler
from channels.routing import ProtocolTypeRouter

application = ProtocolTypeRouter({"http": AsgiHandler})
