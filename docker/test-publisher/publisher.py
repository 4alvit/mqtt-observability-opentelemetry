from __future__ import annotations

import asyncio
import json
import os
import random
import signal
import time
from datetime import UTC, datetime
from typing import Any

import paho.mqtt.client as mqtt
import structlog

logger = structlog.get_logger()

BROKER = os.getenv("MQTT_BROKER", "mosquitto")
PORT = int(os.getenv("MQTT_PORT", "1883"))
INTERVAL = float(os.getenv("PUBLISH_INTERVAL", "5"))
TOPIC_PREFIX = os.getenv("TOPIC_PREFIX", "devices")
DEVICE_COUNT = int(os.getenv("DEVICE_COUNT", "5"))
TRACE_ENABLED = os.getenv("TRACE_ENABLED", "true").lower() == "true"

DEVICE_TYPES = ["sensor", "actuator", "gateway", "controller", "meter"]
SENSOR_TYPES = ["temperature", "humidity", "pressure", "voltage", "current", "power", "energy", "flow"]


class TestPublisher:
    def __init__(self):
        self.client: mqtt.Client | None = None
        self.running = False
        self.devices = [f"{TOPIC_PREFIX}/{dtype}-{i:03d}" for i in range(DEVICE_COUNT) for dtype in DEVICE_TYPES[:1]]
        self.sensor_map = {device: random.choice(SENSOR_TYPES) for device in self.devices}

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: mqtt.ConnectFlags, reason_code: mqtt.ReasonCode, properties: mqtt.Properties | None):
        logger.info("Connected to broker", reason_code=reason_code)

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, reason_code: mqtt.ReasonCode, properties: mqtt.Properties | None):
        logger.warning("Disconnected", reason_code=reason_code)
        if self.running:
            asyncio.create_task(self._reconnect())

    async def _reconnect(self):
        await asyncio.sleep(5)
        if self.running:
            try:
                self.client.reconnect()
            except Exception as e:
                logger.error("Reconnect failed", error=str(e))

    def _generate_payload(self, device: str) -> dict[str, Any]:
        sensor = self.sensor_map[device]
        base_value = {
            "temperature": 20 + random.uniform(-5, 15),
            "humidity": 50 + random.uniform(-20, 30),
            "pressure": 1013 + random.uniform(-20, 20),
            "voltage": 230 + random.uniform(-10, 10),
            "current": 5 + random.uniform(-2, 5),
            "power": 1000 + random.uniform(-200, 500),
            "energy": random.uniform(0, 10000),
            "flow": 10 + random.uniform(-3, 5),
        }[sensor]

        return {
            "device_id": device,
            "sensor_type": sensor,
            "value": round(base_value, 2),
            "unit": {"temperature": "°C", "humidity": "%", "pressure": "hPa", "voltage": "V", "current": "A", "power": "W", "energy": "kWh", "flow": "L/min"}[sensor],
            "timestamp": datetime.now(UTC).isoformat(),
            "metadata": {
                "firmware": "1.2.3",
                "location": f"zone-{random.randint(1, 5)}",
            },
        }

    async def publish_loop(self):
        while self.running:
            for device in self.devices:
                if not self.running:
                    break
                topic = f"{device}/telemetry"
                payload = self._generate_payload(device)

                props = mqtt.Properties(mqtt.PacketTypes.PUBLISH) if TRACE_ENABLED else None
                if TRACE_ENABLED:
                    import uuid
                    trace_id = uuid.uuid4().hex
                    span_id = uuid.uuid4().hex[:16]
                    props.UserProperty = [
                        ("traceparent", f"00-{trace_id}-{span_id}-01"),
                    ]

                self.client.publish(topic, json.dumps(payload), qos=1, properties=props)
                logger.debug("Published", topic=topic, payload=payload)

            await asyncio.sleep(INTERVAL)

    async def start(self):
        self.running = True
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="mqtt-test-publisher",
            protocol=mqtt.MQTTv5,
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

        self.client.connect(BROKER, PORT, 60)
        self.client.loop_start()

        await self.publish_loop()

    async def stop(self):
        self.running = False
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()


async def main():
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
    )

    publisher = TestPublisher()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(publisher.stop()))

    await publisher.start()


if __name__ == "__main__":
    asyncio.run(main())