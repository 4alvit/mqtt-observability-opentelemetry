# MQTT observer

The `mqtt-interceptor` executable is a passive MQTT subscriber. It opens a
Prometheus HTTP endpoint on port 9464 and creates sampled spans for received
messages. It does not listen on port 1884, forward MQTT packets, republish
messages, or inject context into other clients' traffic. Existing MQTT clients
continue connecting directly to their broker.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `MQTT_UPSTREAM_HOST` | `mosquitto` | Existing broker host |
| `MQTT_UPSTREAM_PORT` | `1883` | Existing broker port |
| `MQTT_VERSION` | `5` | `3` selects MQTT 3.1.1; `5` selects MQTT 5 |
| `MQTT_CLIENT_ID` | `mqtt-interceptor` | Use a unique ID per instance |
| `MQTT_USERNAME`, `MQTT_PASSWORD` | unset | Optional broker authentication |
| `MQTT_KEEPALIVE` | `60` | MQTT keepalive in seconds |
| `TRACE_TOPIC_PATTERNS` | `devices/+/telemetry,devices/+/commands` | Comma-separated filters or JSON array |
| `TRACE_SAMPLE_RATE` | `0.1` | Fraction of matching messages exported as spans |
| `OTEL_ENDPOINT` | `http://otelcol:4317` | OTLP gRPC collector endpoint |
| `OTEL_INSECURE` | `true` | Plaintext OTLP when true |
| `OTEL_SERVICE_NAME` | `mqtt-interceptor` | Trace resource service name |
| `OTEL_TIMEOUT` | `10` | Export timeout in seconds |
| `METRICS_ENABLED` | `true` | Enable Prometheus endpoint |
| `METRICS_PORT` | `9464` | Prometheus listener port |

This observer does not currently configure MQTT TLS. Use a trusted broker
network or add supported TLS configuration before connecting over an untrusted
network. OTLP TLS is independent of MQTT transport.

## Subscriptions and trace context

`TRACE_TOPIC_PATTERNS` defines the actual broker subscriptions. Keep the list
narrow: sampling reduces exported spans, not subscribed traffic. The MP overlay
uses only `$SYS/broker/uptime`; it observes broker heartbeats, not device commands
or complete distributed application traces.

When a received MQTT 5 message contains W3C `traceparent`/`tracestate` user
properties, the extracted remote context becomes the observer span's parent.
MQTT 3.1.1 messages without trace properties create independent spans. The
observer exports topic metadata, never message payloads.

## Metrics and readiness

- `mqtt_interceptor_messages_intercepted_total`: received messages.
- `mqtt_interceptor_spans_created_total`: sampled observer spans.
- `mqtt_interceptor_trace_context_extracted_total`: context extraction attempts.
- `mqtt_interceptor_intercept_latency_seconds`: callback processing latency.
- `mqtt_interceptor_last_message_timestamp_seconds`: most recent subscribed
  message's Unix timestamp. With the dedicated heartbeat-only subscription this
  lets the MP readiness check reject missing broker heartbeat traffic.

Readiness of the HTTP endpoint alone does not prove broker connectivity or
successful OTLP delivery. Deployment acceptance must verify a recent message and
retrieve an exported span from the trace backend.

## Example

```yaml
services:
  mqtt-interceptor:
    build: ../mqtt-interceptor
    environment:
      MQTT_UPSTREAM_HOST: mosquitto
      MQTT_VERSION: '3'
      TRACE_TOPIC_PATTERNS: '$$SYS/broker/uptime'
      TRACE_SAMPLE_RATE: '1'
      OTEL_ENDPOINT: http://otelcol:4317
    expose:
      - '9464'
```

The doubled dollar sign is Docker Compose escaping. In Kubernetes use the
literal `$SYS/broker/uptime`. No broker port is exposed by this service.
