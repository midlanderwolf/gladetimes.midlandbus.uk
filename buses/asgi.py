import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from django.urls import path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "buses.settings")

# Initialize Django ASGI application early to ensure the AppRegistry
# is populated before importing code that may import ORM models.
django_asgi_app = get_asgi_application()

from vehicles.consumers import VehicleLocationConsumer


async def lifespan(scope, receive, send):
    # ProtocolTypeRouter raises ValueError on any scope type it doesn't know
    # about, so handle 'lifespan' here - servers like granian send it on
    # startup and would otherwise fail to boot
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": URLRouter(
            [
                path("firehose", VehicleLocationConsumer.as_asgi()),
                path("firehose/<int:vehicle_id>", VehicleLocationConsumer.as_asgi()),
            ]
        ),
        "lifespan": lifespan,
    }
)
