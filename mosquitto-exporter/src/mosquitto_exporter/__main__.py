import asyncio
import logging
import re
import signal
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import paho.mqtt.client as mqtt
import structlog
from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from prometheus_client import start_http_server

from mosquitto_exporter.config import load_config, get_config

logger = structlog.get_logger()


@dataclass
class SYSMetric:
    topic: str
    name: str
    type: str
    description: str
    value_type: type


SYS_METRICS = [
    SYSMetric("$SYS/broker/version", "mosquitto_version", "gauge", "Mosquitto version", str),
    SYSMetric("$SYS/broker/uptime", "mosquitto_uptime_seconds", "gauge", "Broker uptime in seconds", int),
    SYSMetric("$SYS/broker/timestamp", "mosquitto_timestamp", "gauge", "Broker timestamp", int),
    SYSMetric("$SYS/broker/load/messages/received/1min", "mosquitto_messages_received_1min", "gauge", "Messages received per second (1min avg)", float),
    SYSMetric("$SYS/broker/load/messages/received/5min", "mosquitto_messages_received_5min", "gauge", "Messages received per second (5min avg)", float),
    SYSMetric("$SYS/broker/load/messages/received/15min", "mosquitto_messages_received_15min", "gauge", "Messages received per second (15min avg)", float),
    SYSMetric("$SYS/broker/load/messages/sent/1min", "mosquitto_messages_sent_1min", "gauge", "Messages sent per second (1min avg)", float),
    SYSMetric("$SYS/broker/load/messages/sent/5min", "mosquitto_messages_sent_5min", "gauge", "Messages sent per second (5min avg)", float),
    SYSMetric("$SYS/broker/load/messages/sent/15min", "mosquitto_messages_sent_15min", "gauge", "Messages sent per second (15min avg)", float),
    SYSMetric("$SYS/broker/load/bytes/received/1min", "mosquitto_bytes_received_1min", "gauge", "Bytes received per second (1min avg)", float),
    SYSMetric("$SYS/broker/load/bytes/received/5min", "mosquitto_bytes_received_5min", "gauge", "Bytes received per second (5min avg)", float),
    SYSMetric("$SYS/broker/load/bytes/received/15min", "mosquitto_bytes_received_15min", "gauge", "Bytes received per second (15min avg)", float),
    SYSMetric("$SYS/broker/load/bytes/sent/1min", "mosquitto_bytes_sent_1min", "gauge", "Bytes sent per second (1min avg)", float),
    SYSMetric("$SYS/broker/load/bytes/sent/5min", "mosquitto_bytes_sent_5min", "gauge", "Bytes sent per second (5min avg)", float),
    SYSMetric("$SYS/broker/load/bytes/sent/15min", "mosquitto_bytes_sent_15min", "gauge", "Bytes sent per second (15min avg)", float),
    SYSMetric("$SYS/broker/clients/connected", "mosquitto_clients_connected", "gauge", "Connected clients", int),
    SYSMetric("$SYS/broker/clients/disconnected", "mosquitto_clients_disconnected", "counter", "Disconnected clients", int),
    SYSMetric("$SYS/broker/clients/expired", "mosquitto_clients_expired", "counter", "Expired sessions", int),
    SYSMetric("$SYS/broker/clients/maximum", "mosquitto_clients_maximum", "gauge", "Maximum clients", int),
    SYSMetric("$SYS/broker/subscriptions/count", "mosquitto_subscriptions_count", "gauge", "Active subscriptions", int),
    SYSMetric("$SYS/broker/retained messages/count", "mosquitto_retained_messages_count", "gauge", "Retained messages", int),
    SYSMetric("$SYS/broker/messages/received", "mosquitto_messages_received_total", "counter", "Total messages received", int),
    SYSMetric("$SYS/broker/messages/sent", "mosquitto_messages_sent_total", "counter", "Total messages sent", int),
    SYSMetric("$SYS/broker/messages/dropped", "mosquitto_messages_dropped_total", "counter", "Total messages dropped", int),
    SYSMetric("$SYS/broker/messages/inflight", "mosquitto_messages_inflight", "gauge", "In-flight messages", int),
    SYSMetric("$SYS/broker/bytes/received", "mosquitto_bytes_received_total", "counter", "Total bytes received", int),
    SYSMetric("$SYS/broker/bytes/sent", "mosquitto_bytes_sent_total", "counter", "Total bytes sent", int),
    SYSMetric("$SYS/broker/publish/messages/received", "mosquitto_publish_received_total", "counter", "PUBLISH messages received", int),
    SYSMetric("$SYS/broker/publish/messages/sent", "mosquitto_publish_sent_total", "counter", "PUBLISH messages sent", int),
    SYSMetric("$SYS/broker/publish/bytes/received", "mosquitto_publish_bytes_received_total", "counter", "PUBLISH bytes received", int),
    SYSMetric("$SYS/broker/publish/bytes/sent", "mosquitto_publish_bytes_sent_total", "counter", "PUBLISH bytes sent", int),
    SYSMetric("$SYS/broker/publish/dropped", "mosquitto_publish_dropped_total", "counter", "PUBLISH messages dropped", int),
]


class SYSMetricsCollector:
    def __init__(self, config):
        self.config = config
        self.client = None
        self.metrics_data: dict[str, Any] = {}
        self.last_update: float = 0
        self._setup_otel()

    def _setup_otel(self):
        resource = Resource.create({
            "service.name": self.config.otel.service_name,
            "service.version": "0.1.0",
            **self.config.otel.resource_attributes,
        })

        readers = []

        if self.config.prometheus.enabled:
            prometheus_reader = PrometheusMetricReader()
            readers.append(prometheus_reader)

        if self.config.otel.endpoint:
            otlp_exporter = OTLPMetricExporter(
                endpoint=self.config.otel.endpoint,
                insecure=self.config.otel.insecure,
                timeout=self.config.otel.timeout,
            )
            readers.append(PeriodicExportingMetricReader(
                otlp_exporter,
                export_interval_millis=self.config.otel.export_interval_ms,
            ))

        provider = MeterProvider(resource=resource, metric_readers=readers)
        metrics.set_meter_provider(provider)
        self.meter = metrics.get_meter(__name__, "0.1.0")
        self._create_otel_metrics()
        self._register_callbacks()

    def _create_otel_metrics(self):
        self.otel_metrics = {}
        for sys_metric in SYS_METRICS:
            if sys_metric.type == "counter":
                self.otel_metrics[sys_metric.name] = self.meter.create_counter(
                    sys_metric.name,
                    description=sys_metric.description,
                    unit=sys_metric.name.split("_")[-1] if "_" in sys_metric.name else "1",
                )
            else:
                self.otel_metrics[sys_metric.name] = self.meter.create_gauge(
                    sys_metric.name,
                    description=sys_metric.description,
                    unit=sys_metric.name.split("_")[-1] if "_" in sys_metric.name else "1",
                )

    def _register_callbacks(self):
        gauge_metrics = {k: v for k, v in self.otel_metrics.items() if isinstance(v, type(self.meter.create_gauge("")))}
        if gauge_metrics:
            self.meter.create_observable_gauge(
                "mosquitto_gauges",
                callbacks=[self._observable_callback],
            )

    def _observable_callback(self, options: CallbackOptions) -> list[Observation]:
        observations = []
        for name, metric in self.otel_metrics.items():
            if name in self.metrics_data:
                value = self.metrics_data[name]
                if isinstance(value, (int, float)):
                    observations.append(Observation(value, attributes={}))
        return observations

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: mqtt.ConnectFlags, reason_code: mqtt.ReasonCode, properties: mqtt.Properties | None):
        logger.info("Connected to Mosquitto", reason_code=reason_code)
        topics = [m.topic for m in SYS_METRICS]
        for topic in topics:
            client.subscribe(topic, qos=0)

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage):
        try:
            value = msg.payload.decode().strip()
            self._parse_and_store(msg.topic, value)
        except Exception as e:
            logger.warning("Failed to parse message", topic=msg.topic, error=str(e))

    def _parse_and_store(self, topic: str, value: str):
        for sys_metric in SYS_METRICS:
            if topic == sys_metric.topic:
                try:
                    parsed = sys_metric.value_type(value)
                    self.metrics_data[sys_metric.name] = parsed
                    self._record_metric(sys_metric.name, parsed, sys_metric.type)
                    self.last_update = time.time()
                except ValueError:
                    logger.warning("Failed to parse value", topic=topic, value=value, type=sys_metric.value_type.__name__)
                break

    def _record_metric(self, name: str, value: int | float, metric_type: str):
        metric = self.otel_metrics.get(name)
        if metric and metric_type == "counter":
            metric.add(value)
        elif metric and metric_type == "gauge":
            pass

    async def start(self):
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.config.mqtt.client_id,
            protocol=mqtt.MQTTv5,
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        if self.config.mqtt.username:
            self.client.username_pw_set(self.config.mqtt.username, self.config.mqtt.password)

        logger.info("Connecting to Mosquitto", host=self.config.mqtt.upstream_host, port=self.config.mqtt.upstream_port)
        self.client.connect_async(self.config.mqtt.upstream_host, self.config.mqtt.upstream_port, keepalive=self.config.mqtt.keepalive)
        self.client.loop_start()

        if self.config.prometheus.enabled:
            start_http_server(self.config.prometheus.port, addr="0.0.0.0")
            logger.info("Prometheus metrics server started", port=self.config.prometheus.port)

        try:
            while True:
                await asyncio.sleep(self.config.metrics.scrape_interval)
                if time.time() - self.last_update > self.config.metrics.stale_threshold:
                    logger.warning("No metrics received recently", last_update=self.last_update)
        except asyncio.CancelledError:
            logger.info("Collector stopped")
        finally:
            self.stop()

    def stop(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()


async def main():
    config = load_config()
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(config.logging.level),
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer() if config.logging.format == "json" else structlog.dev.ConsoleRenderer(),
        ],
    )

    collector = SYSMetricsCollector(config)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(collector)))

    await collector.start()


async def shutdown(collector: SYSMetricsCollector):
    logger.info("Shutting down...")
    collector.stop()


if __name__ == "__main__":
    asyncio.run(main())