"""Collect broker statistics and export numeric OpenTelemetry measurements."""

import asyncio
import builtins
import math
import signal
import threading
import time
from dataclasses import dataclass, field
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

from mosquitto_exporter._version import __version__
from mosquitto_exporter.config import Config, load_config

logger = structlog.get_logger()


@dataclass
class SYSMetric:
    """Describe a broker statistics topic and its exported numeric instrument."""

    topic: str
    name: str
    type: str
    description: str
    value_type: builtins.type
    attributes: dict[str, str] = field(default_factory=dict)


SYS_METRICS = [
    SYSMetric("$SYS/broker/version", "mosquitto_version", "gauge", "Mosquitto version", str),
    SYSMetric(
        "$SYS/broker/uptime", "mosquitto_uptime_seconds", "gauge", "Broker uptime in seconds", int
    ),
    SYSMetric("$SYS/broker/timestamp", "mosquitto_timestamp", "gauge", "Broker timestamp", int),
    SYSMetric(
        "$SYS/broker/load/messages/received/1min",
        "mosquitto_messages_received_1min",
        "gauge",
        "Messages received per second (1min avg)",
        float,
    ),
    SYSMetric(
        "$SYS/broker/load/messages/received/5min",
        "mosquitto_messages_received_5min",
        "gauge",
        "Messages received per second (5min avg)",
        float,
    ),
    SYSMetric(
        "$SYS/broker/load/messages/received/15min",
        "mosquitto_messages_received_15min",
        "gauge",
        "Messages received per second (15min avg)",
        float,
    ),
    SYSMetric(
        "$SYS/broker/load/messages/sent/1min",
        "mosquitto_messages_sent_1min",
        "gauge",
        "Messages sent per second (1min avg)",
        float,
    ),
    SYSMetric(
        "$SYS/broker/load/messages/sent/5min",
        "mosquitto_messages_sent_5min",
        "gauge",
        "Messages sent per second (5min avg)",
        float,
    ),
    SYSMetric(
        "$SYS/broker/load/messages/sent/15min",
        "mosquitto_messages_sent_15min",
        "gauge",
        "Messages sent per second (15min avg)",
        float,
    ),
    SYSMetric(
        "$SYS/broker/load/bytes/received/1min",
        "mosquitto_bytes_received_1min",
        "gauge",
        "Bytes received per second (1min avg)",
        float,
    ),
    SYSMetric(
        "$SYS/broker/load/bytes/received/5min",
        "mosquitto_bytes_received_5min",
        "gauge",
        "Bytes received per second (5min avg)",
        float,
    ),
    SYSMetric(
        "$SYS/broker/load/bytes/received/15min",
        "mosquitto_bytes_received_15min",
        "gauge",
        "Bytes received per second (15min avg)",
        float,
    ),
    SYSMetric(
        "$SYS/broker/load/bytes/sent/1min",
        "mosquitto_bytes_sent_1min",
        "gauge",
        "Bytes sent per second (1min avg)",
        float,
    ),
    SYSMetric(
        "$SYS/broker/load/bytes/sent/5min",
        "mosquitto_bytes_sent_5min",
        "gauge",
        "Bytes sent per second (5min avg)",
        float,
    ),
    SYSMetric(
        "$SYS/broker/load/bytes/sent/15min",
        "mosquitto_bytes_sent_15min",
        "gauge",
        "Bytes sent per second (15min avg)",
        float,
    ),
    SYSMetric(
        "$SYS/broker/clients/connected",
        "mosquitto_clients_connected",
        "gauge",
        "Connected clients",
        int,
    ),
    SYSMetric(
        "$SYS/broker/clients/disconnected",
        "mosquitto_clients_disconnected",
        "gauge",
        "Disconnected clients",
        int,
    ),
    SYSMetric(
        "$SYS/broker/clients/expired",
        "mosquitto_clients_expired",
        "counter",
        "Expired sessions",
        int,
    ),
    SYSMetric(
        "$SYS/broker/clients/maximum", "mosquitto_clients_maximum", "gauge", "Maximum clients", int
    ),
    SYSMetric(
        "$SYS/broker/subscriptions/count",
        "mosquitto_subscriptions_count",
        "gauge",
        "Active subscriptions",
        int,
    ),
    SYSMetric(
        "$SYS/broker/retained messages/count",
        "mosquitto_retained_messages_count",
        "gauge",
        "Retained messages",
        int,
    ),
    SYSMetric(
        "$SYS/broker/messages/received",
        "mosquitto_messages_received_total",
        "counter",
        "Total messages received",
        int,
    ),
    SYSMetric(
        "$SYS/broker/messages/sent",
        "mosquitto_messages_sent_total",
        "counter",
        "Total messages sent",
        int,
    ),
    SYSMetric(
        "$SYS/broker/messages/dropped",
        "mosquitto_messages_dropped_total",
        "counter",
        "Total messages dropped",
        int,
    ),
    SYSMetric(
        "$SYS/broker/messages/inflight",
        "mosquitto_messages_inflight",
        "gauge",
        "In-flight messages",
        int,
    ),
    SYSMetric(
        "$SYS/broker/bytes/received",
        "mosquitto_bytes_received_total",
        "counter",
        "Total bytes received",
        int,
    ),
    SYSMetric(
        "$SYS/broker/bytes/sent", "mosquitto_bytes_sent_total", "counter", "Total bytes sent", int
    ),
    SYSMetric(
        "$SYS/broker/publish/messages/received",
        "mosquitto_publish_received_total",
        "counter",
        "PUBLISH messages received",
        int,
    ),
    SYSMetric(
        "$SYS/broker/publish/messages/sent",
        "mosquitto_publish_sent_total",
        "counter",
        "PUBLISH messages sent",
        int,
    ),
    SYSMetric(
        "$SYS/broker/publish/bytes/received",
        "mosquitto_publish_bytes_received_total",
        "counter",
        "PUBLISH bytes received",
        int,
    ),
    SYSMetric(
        "$SYS/broker/publish/bytes/sent",
        "mosquitto_publish_bytes_sent_total",
        "counter",
        "PUBLISH bytes sent",
        int,
    ),
    SYSMetric(
        "$SYS/broker/publish/dropped",
        "mosquitto_publish_dropped_total",
        "counter",
        "PUBLISH messages dropped",
        int,
    ),
]


def flashmq_metrics(thread_count: int) -> list[SYSMetric]:
    """Select FlashMQ's exact statistics topics, with bounded worker series."""
    metrics_list = [
        SYSMetric(
            "$SYS/broker/clients/total",
            "flashmq_clients_connected",
            "gauge",
            "Connected FlashMQ clients",
            int,
        ),
        SYSMetric(
            "$SYS/broker/load/messages/received/total",
            "flashmq_messages_received_total",
            "counter",
            "Total messages received",
            int,
        ),
        SYSMetric(
            "$SYS/broker/load/messages/sent/total",
            "flashmq_messages_sent_total",
            "counter",
            "Total messages sent",
            int,
        ),
        SYSMetric(
            "$SYS/broker/load/messages/received/persecond",
            "flashmq_messages_received_per_second",
            "gauge",
            "Broker-reported received messages per second",
            float,
        ),
        SYSMetric(
            "$SYS/broker/load/messages/sent/persecond",
            "flashmq_messages_sent_per_second",
            "gauge",
            "Broker-reported sent messages per second",
            float,
        ),
        SYSMetric(
            "$SYS/broker/subscriptions/count",
            "flashmq_subscriptions_count",
            "gauge",
            "Current subscriptions",
            int,
        ),
        SYSMetric(
            "$SYS/broker/retained messages/count",
            "flashmq_retained_messages_count",
            "gauge",
            "Current retained messages",
            int,
        ),
        SYSMetric(
            "$SYS/broker/sessions/total", "flashmq_sessions_count", "gauge", "Current sessions", int
        ),
    ]
    for thread in range(thread_count):
        for suffix, name, description in (
            ("latest__ms", "flashmq_thread_drift_milliseconds", "Latest worker event-loop drift"),
            (
                "moving_avg__ms",
                "flashmq_thread_drift_moving_average_milliseconds",
                "Moving average of worker event-loop drift",
            ),
        ):
            metrics_list.append(
                SYSMetric(
                    f"$SYS/broker/threads/{thread}/drift/{suffix}",
                    name,
                    "gauge",
                    description,
                    int,
                    {"thread": str(thread)},
                )
            )
    return metrics_list


class SYSMetricsCollector:
    """Receive broker statistics and expose them through configured metric readers."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.sys_metrics = (
            flashmq_metrics(config.metrics.flashmq_threads)
            if config.metrics.broker_type == "flashmq"
            else SYS_METRICS
        )
        self.client: mqtt.Client | None = None
        self.metrics_data: dict[str, Any] = {}
        self.last_update: float = 0
        self.otel_metrics: dict[str, Any] = {}
        self._values: dict[str, int | float] = {}
        self._counter_epochs: dict[str, int] = {}
        self._value_updates: dict[str, float] = {}
        self._values_lock = threading.Lock()
        self._stop_event = asyncio.Event()
        self._setup_otel()

    def _setup_otel(self) -> None:
        resource = Resource.create(
            {
                "service.name": self.config.otel.service_name,
                "service.version": __version__,
                **self.config.otel.resource_attributes,
            }
        )

        readers: list[PrometheusMetricReader | PeriodicExportingMetricReader] = []

        if self.config.prometheus.enabled:
            prometheus_reader = PrometheusMetricReader()
            readers.append(prometheus_reader)

        if self.config.otel.endpoint:
            otlp_exporter = OTLPMetricExporter(
                endpoint=self.config.otel.endpoint,
                insecure=self.config.otel.insecure,
                timeout=self.config.otel.timeout,
            )
            readers.append(
                PeriodicExportingMetricReader(
                    otlp_exporter,
                    export_interval_millis=self.config.otel.export_interval_ms,
                )
            )

        provider = MeterProvider(resource=resource, metric_readers=readers)
        metrics.set_meter_provider(provider)
        self.meter = metrics.get_meter(__name__, __version__)
        self._create_otel_metrics()

    def _create_otel_metrics(self) -> None:
        self.otel_metrics = {}
        groups: dict[str, list[SYSMetric]] = {}
        for sys_metric in self.sys_metrics:
            if sys_metric.value_type is not str:
                groups.setdefault(sys_metric.name, []).append(sys_metric)
        for name, definitions in groups.items():

            def observe(
                _options: CallbackOptions, series: list[SYSMetric] = definitions
            ) -> list[Observation]:
                with self._values_lock:
                    now = time.monotonic()
                    observations = []
                    for definition in series:
                        updated = (
                            self._value_updates.get(definition.topic, 0)
                            if self.config.metrics.broker_type == "flashmq"
                            else self.last_update
                        )
                        value = self._values.get(definition.topic)
                        if (
                            value is not None
                            and now - updated <= self.config.metrics.stale_threshold
                        ):
                            attributes: dict[str, str | int] = dict(definition.attributes)
                            if definition.type == "counter":
                                attributes["counter_epoch"] = self._counter_epochs.get(
                                    definition.topic, 0
                                )
                            observations.append(Observation(value, attributes=attributes))
                    return observations

            # $SYS publishes absolute broker totals. Observable instruments report
            # the current snapshot without adding it twice. A broker reset gets
            # a new stream identity so delta readers never compute a negative sum.
            create = (
                self.meter.create_observable_counter
                if definitions[0].type == "counter"
                else self.meter.create_observable_gauge
            )
            self.otel_metrics[name] = create(
                name, callbacks=[observe], description=definitions[0].description
            )

    def _on_connect(
        self,
        client: mqtt.Client,
        _userdata: Any,
        _flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        logger.info(
            "Connected to MQTT broker",
            broker_type=self.config.metrics.broker_type,
            reason_code=reason_code,
        )
        topics = [m.topic for m in self.sys_metrics]
        for topic in topics:
            client.subscribe(topic, qos=0)

    def _on_message(self, _client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        try:
            value = msg.payload.decode().strip()
            self._parse_and_store(msg.topic, value)
        # Isolate malformed messages and exporter failures from the MQTT network loop.
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to parse message", topic=msg.topic, error=str(e), exc_info=True)

    def _parse_and_store(self, topic: str, value: str) -> None:
        for sys_metric in self.sys_metrics:
            if topic == sys_metric.topic:
                try:
                    if sys_metric.topic == "$SYS/broker/uptime":
                        value = value.removesuffix(" seconds")
                    parsed = sys_metric.value_type(value)
                    if isinstance(parsed, (int, float)) and (
                        not math.isfinite(parsed) or parsed < 0
                    ):
                        raise ValueError("Expected a finite nonnegative broker statistic")
                    if sys_metric.attributes:
                        self.metrics_data.setdefault(sys_metric.name, {})[
                            sys_metric.attributes["thread"]
                        ] = parsed
                    else:
                        self.metrics_data[sys_metric.name] = parsed
                    self._record_metric(sys_metric.topic, parsed, sys_metric.type)
                    self.last_update = time.monotonic()
                except ValueError:
                    logger.warning(
                        "Failed to parse value",
                        topic=topic,
                        value=value,
                        type=sys_metric.value_type.__name__,
                    )
                break

    def _record_metric(self, topic: str, value: int | float, metric_type: str) -> None:
        # Broker version is text metadata, not a numeric gauge sample.
        if not isinstance(value, (int, float)):
            return
        with self._values_lock:
            previous = self._values.get(topic)
            if metric_type == "counter" and previous is not None and value < previous:
                self._counter_epochs[topic] = self._counter_epochs.get(topic, 0) + 1
            self._values[topic] = value
            self._value_updates[topic] = time.monotonic()

    async def start(self) -> None:
        """Connect to the broker and run periodic metric exports until cancelled."""
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.config.mqtt.client_id,
            protocol=mqtt.MQTTv5 if self.config.mqtt.version == 5 else mqtt.MQTTv311,
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        if self.config.mqtt.username:
            self.client.username_pw_set(self.config.mqtt.username, self.config.mqtt.password)

        if self.config.mqtt.tls_enabled:
            self.client.tls_set(
                ca_certs=self.config.mqtt.tls_ca_cert,
                certfile=self.config.mqtt.tls_certfile,
                keyfile=self.config.mqtt.tls_keyfile,
            )

        logger.info(
            "Connecting to MQTT broker",
            broker_type=self.config.metrics.broker_type,
            host=self.config.mqtt.upstream_host,
            port=self.config.mqtt.upstream_port,
        )
        self.client.connect_async(
            self.config.mqtt.upstream_host,
            self.config.mqtt.upstream_port,
            keepalive=self.config.mqtt.keepalive,
        )
        self.client.loop_start()

        if self.config.prometheus.enabled:
            start_http_server(self.config.prometheus.port, addr="0.0.0.0")
            logger.info("Prometheus metrics server started", port=self.config.prometheus.port)

        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.config.metrics.scrape_interval
                    )
                except TimeoutError:
                    pass
                if time.monotonic() - self.last_update > self.config.metrics.stale_threshold:
                    logger.warning("No metrics received recently", last_update=self.last_update)
        except asyncio.CancelledError:
            logger.info("Collector stopped")
        finally:
            self.stop()

    def stop(self) -> None:
        """Stop the MQTT network loop and disconnect the collector."""
        self._stop_event.set()
        if self.client:
            self.client.disconnect()
            self.client.loop_stop()
            self.client = None


async def main() -> None:
    """Configure logging and coordinate collector shutdown on process signals."""
    config = load_config()
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(config.logging.level),
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer()
            if config.logging.format == "json"
            else structlog.dev.ConsoleRenderer(),
        ],
    )

    collector = SYSMetricsCollector(config)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(collector)))

    await collector.start()


async def shutdown(collector: SYSMetricsCollector) -> None:
    """Disconnect the collector in response to a process shutdown signal."""
    logger.info("Shutting down...")
    collector.stop()
