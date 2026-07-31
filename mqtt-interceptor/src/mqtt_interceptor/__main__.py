import asyncio
import logging
import signal
from typing import Any

import paho.mqtt.client as mqtt
import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import SpanKind, TraceFlags, TraceState
from prometheus_client import Counter, Histogram, start_http_server

from mqtt_interceptor.config import Config, load_config

logger = structlog.get_logger()

# Metrics
messages_intercepted = Counter(
    "mqtt_interceptor_messages_intercepted_total",
    "Total messages intercepted",
    ["direction", "topic_pattern"],
)
span_created = Counter(
    "mqtt_interceptor_spans_created_total",
    "Total spans created",
    ["topic_pattern", "span_kind"],
)
trace_context_extracted = Counter(
    "mqtt_interceptor_trace_context_extracted_total",
    "Trace context extracted from messages",
    ["propagator", "success"],
)
trace_context_injected = Counter(
    "mqtt_interceptor_trace_context_injected_total",
    "Trace context injected into messages",
    ["propagator"],
)
intercept_latency = Histogram(
    "mqtt_interceptor_intercept_latency_seconds",
    "Message intercept latency",
    ["direction"],
)


class TraceContextPropagator:
    """W3C Trace Context propagator for MQTT messages."""

    TRACEPARENT_KEY = "traceparent"
    TRACESTATE_KEY = "tracestate"

    def __init__(self, propagator_type: str = "w3c"):
        self.propagator_type = propagator_type

    def extract(
        self, properties: mqtt.Properties | None, user_properties: list[tuple[str, str]] | None
    ) -> trace.SpanContext | None:
        """Extract trace context from MQTT message properties."""
        if not properties and not user_properties:
            trace_context_extracted.labels(propagator=self.propagator_type, success="false").inc()
            return None

        traceparent = None
        tracestate = None

        if properties and hasattr(properties, "UserProperty"):
            for k, v in properties.UserProperty or []:
                if k == self.TRACEPARENT_KEY:
                    traceparent = v
                elif k == self.TRACESTATE_KEY:
                    tracestate = v

        if user_properties:
            for k, v in user_properties:
                if k == self.TRACEPARENT_KEY:
                    traceparent = v
                elif k == self.TRACESTATE_KEY:
                    tracestate = v

        if not traceparent:
            trace_context_extracted.labels(propagator=self.propagator_type, success="false").inc()
            return None

        try:
            parts = traceparent.split("-")
            if len(parts) != 4:
                raise ValueError("Invalid traceparent format")

            version, trace_id, parent_id, flags = parts
            trace_flags = TraceFlags(int(flags, 16))
            trace_state = TraceState.from_header(tracestate) if tracestate else TraceState()

            span_context = trace.SpanContext(
                trace_id=int(trace_id, 16),
                span_id=int(parent_id, 16),
                is_remote=True,
                trace_flags=trace_flags,
                trace_state=trace_state,
            )
            trace_context_extracted.labels(propagator=self.propagator_type, success="true").inc()
            return span_context
        except Exception as e:
            logger.warning("Failed to extract trace context", error=str(e), traceparent=traceparent)
            trace_context_extracted.labels(propagator=self.propagator_type, success="false").inc()
            return None

    def inject(
        self,
        properties: mqtt.Properties | None,
        user_properties: list[tuple[str, str]] | None,
        span_context: trace.SpanContext,
    ) -> tuple[mqtt.Properties | None, list[tuple[str, str]] | None]:
        """Inject trace context into MQTT message properties."""
        traceparent = f"00-{span_context.trace_id:032x}-{span_context.span_id:016x}-{span_context.trace_flags:02x}"
        tracestate = span_context.trace_state.to_header() if span_context.trace_state else ""

        new_user_properties = list(user_properties) if user_properties else []
        new_user_properties.append((self.TRACEPARENT_KEY, traceparent))
        if tracestate:
            new_user_properties.append((self.TRACESTATE_KEY, tracestate))

        trace_context_injected.labels(propagator=self.propagator_type).inc()
        return properties, new_user_properties


class TopicSpanProcessor:
    """Creates spans based on MQTT topic patterns."""

    def __init__(self, tracer: trace.Tracer, topic_patterns: list[str], sample_rate: float = 0.1):
        self.tracer = tracer
        self.topic_patterns = topic_patterns
        self.sample_rate = sample_rate
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        import re

        self.compiled_patterns = []
        for pattern in self.topic_patterns:
            regex = pattern.replace("+", "[^/]+").replace("#", ".*")
            self.compiled_patterns.append(re.compile(f"^{regex}$"))

    def matches(self, topic: str) -> tuple[bool, str | None]:
        for i, pattern in enumerate(self.compiled_patterns):
            if pattern.match(topic):
                return True, self.topic_patterns[i]
        return False, None

    def extract_attributes(self, topic: str, pattern: str) -> dict[str, str]:
        attrs = {"mqtt.topic": topic, "mqtt.topic_pattern": pattern}
        topic_parts = topic.split("/")
        pattern_parts = pattern.split("/")
        for i, (t, p) in enumerate(zip(topic_parts, pattern_parts, strict=False)):
            if p == "+":
                attrs[f"mqtt.topic_segment_{i}"] = t
            elif p == "#":
                attrs[f"mqtt.topic_suffix_{i}"] = "/".join(topic_parts[i:])
                break
        return attrs

    def should_sample(self) -> bool:
        import random

        return random.random() < self.sample_rate

    def create_span(
        self, topic: str, direction: str, attributes: dict[str, str] | None = None
    ) -> trace.Span | None:
        matched, pattern = self.matches(topic)
        if not matched or pattern is None or not self.should_sample():
            return None

        attrs = self.extract_attributes(topic, pattern)
        if attributes:
            attrs.update(attributes)

        span_name = f"mqtt.{direction}.{pattern}"
        if direction == "publish":
            kind = SpanKind.PRODUCER
        else:
            kind = SpanKind.CONSUMER

        span = self.tracer.start_span(span_name, kind=kind, attributes=attrs)
        span_created.labels(topic_pattern=pattern, span_kind=direction).inc()
        return span


class MQTTInterceptor:
    """MQTT proxy that intercepts messages for tracing and metrics."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.running = False
        self.client: mqtt.Client | None = None
        self.upstream_client: mqtt.Client | None = None
        self.propagator = TraceContextPropagator(config.trace.propagator)
        self.tracer: trace.Tracer | None = None
        self.span_processor: TopicSpanProcessor | None = None
        self._setup_tracing()

    def _setup_tracing(self) -> None:
        resource = Resource.create(
            {
                "service.name": self.config.otel.service_name,
                "service.version": self.config.otel.service_version,
                **self.config.otel.resource_attributes,
            }
        )
        provider = TracerProvider(resource=resource)

        exporter: OTLPSpanExporter | ConsoleSpanExporter
        if self.config.otel.endpoint:
            exporter = OTLPSpanExporter(
                endpoint=self.config.otel.endpoint,
                insecure=self.config.otel.insecure,
                timeout=self.config.otel.timeout,
            )
        else:
            exporter = ConsoleSpanExporter()

        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        self.tracer = trace.get_tracer(__name__, self.config.otel.service_version)
        self.span_processor = TopicSpanProcessor(
            self.tracer,
            self.config.trace.topic_patterns,
            self.config.trace.sample_rate,
        )

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        logger.info("Interceptor connected", reason_code=reason_code)
        client.subscribe("#", qos=2)

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        with intercept_latency.labels(direction="subscribe").time():
            self._handle_publish(msg)

    def _handle_publish(self, msg: mqtt.MQTTMessage) -> None:
        messages_intercepted.labels(direction="subscribe", topic_pattern="all").inc()

        span_context = self.propagator.extract(
            msg.properties, getattr(msg, "user_properties", None)
        )
        if span_context is None:
            current_context = trace.get_current_span().get_span_context()
            if current_context.is_valid:
                span_context = current_context

        if self.span_processor:
            attrs = {"mqtt.qos": str(msg.qos), "mqtt.retain": str(msg.retain)}
            span = self.span_processor.create_span(msg.topic, "subscribe", attrs)
            if span:
                with trace.use_span(span, end_on_exit=True):
                    self._forward_to_upstream(msg, span.get_span_context())
                return

        self._forward_to_upstream(msg, span_context)

    def _forward_to_upstream(
        self, msg: mqtt.MQTTMessage, span_context: trace.SpanContext | None
    ) -> None:
        if not self.upstream_client or not self.upstream_client.is_connected():
            logger.warning("Upstream not connected, dropping message")
            return

        props: mqtt.Properties | None
        if self.config.trace.propagate_on_publish and span_context:
            props = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
            _, user_props = self.propagator.inject(
                None, getattr(msg, "user_properties", None), span_context
            )
            if user_props:
                props.UserProperty = user_props
        else:
            props = msg.properties
            user_props = getattr(msg, "user_properties", None)

        try:
            self.upstream_client.publish(
                msg.topic,
                msg.payload,
                qos=msg.qos,
                retain=msg.retain,
                properties=props,
            )
        except Exception as e:
            logger.error("Failed to forward message", error=str(e), topic=msg.topic)

    def _on_upstream_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        logger.info("Upstream connected", reason_code=reason_code)

    def _on_upstream_message(
        self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage
    ) -> None:
        with intercept_latency.labels(direction="publish").time():
            messages_intercepted.labels(direction="publish", topic_pattern="all").inc()
            self._forward_to_downstream(msg)

    def _forward_to_downstream(self, msg: mqtt.MQTTMessage) -> None:
        if not self.client or not self.client.is_connected():
            return

        props: mqtt.Properties | None
        if self.config.trace.propagate_on_subscribe:
            span_context = trace.get_current_span().get_span_context()
            if span_context.is_valid:
                props = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
                _, user_props = self.propagator.inject(
                    None, getattr(msg, "user_properties", None), span_context
                )
                if user_props:
                    props.UserProperty = user_props
            else:
                props = msg.properties
        else:
            props = msg.properties

        try:
            self.client.publish(
                msg.topic,
                msg.payload,
                qos=msg.qos,
                retain=msg.retain,
                properties=props,
            )
        except Exception as e:
            logger.error("Failed to forward to downstream", error=str(e), topic=msg.topic)

    async def start(self) -> None:
        self.running = True

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.config.mqtt.client_id,
            protocol=mqtt.MQTTv5 if self.config.mqtt.version == 5 else mqtt.MQTTv311,
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        if self.config.mqtt.username:
            self.client.username_pw_set(self.config.mqtt.username, self.config.mqtt.password)

        self.upstream_client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"{self.config.mqtt.client_id}-upstream",
            protocol=mqtt.MQTTv5 if self.config.mqtt.version == 5 else mqtt.MQTTv311,
        )
        self.upstream_client.on_connect = self._on_upstream_connect
        self.upstream_client.on_message = self._on_upstream_message
        self.upstream_client.subscribe("#", qos=2)

        if self.config.mqtt.username:
            self.upstream_client.username_pw_set(
                self.config.mqtt.username, self.config.mqtt.password
            )

        await asyncio.get_event_loop().run_in_executor(
            None,
            self.upstream_client.connect,
            self.config.mqtt.upstream_host,
            self.config.mqtt.upstream_port,
            self.config.mqtt.keepalive,
        )
        self.upstream_client.loop_start()

        await asyncio.get_event_loop().run_in_executor(
            None,
            self.client.connect,
            self.config.mqtt.listen_host,
            self.config.mqtt.listen_port,
            self.config.mqtt.keepalive,
        )
        self.client.loop_start()

        if self.config.metrics.enabled:
            start_http_server(self.config.metrics.port)
            logger.info("Metrics server started", port=self.config.metrics.port)

        logger.info(
            "MQTT Interceptor started",
            listen=self.config.mqtt.listen_address,
            upstream=self.config.mqtt.upstream_address,
        )

    async def stop(self) -> None:
        self.running = False
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
        if self.upstream_client:
            self.upstream_client.loop_stop()
            self.upstream_client.disconnect()
        logger.info("MQTT Interceptor stopped")


async def main(config_path: str | None = None) -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )

    config = load_config(config_path)
    interceptor = MQTTInterceptor(config)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(interceptor.stop()))

    await interceptor.start()

    while interceptor.running:
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
