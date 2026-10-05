from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .utils import VEHICLE_WATCHERS_KEY, async_redis_client


# firehose
class VehicleLocationConsumer(AsyncJsonWebsocketConsumer):
    @property
    def vehicle_id(self):
        return self.scope["url_route"]["kwargs"].get("vehicle_id")

    @property
    def group(self):
        if self.vehicle_id:
            return f"vehicle_location{self.vehicle_id}"
        return "vehicle_locations"

    async def connect(self):
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

        if self.vehicle_id and async_redis_client:
            item = await async_redis_client.get(f"vehicle{self.vehicle_id}")
            if item:
                await self.send(text_data=f'{{"items": [{item.decode()}]}}')

            await async_redis_client.zincrby(VEHICLE_WATCHERS_KEY, 1, self.vehicle_id)

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group, self.channel_name)

        if self.vehicle_id and async_redis_client:
            await async_redis_client.zincrby(VEHICLE_WATCHERS_KEY, -1, self.vehicle_id)

    async def move_vehicles(self, event):
        await self.send_json({"items": event["items"]})
