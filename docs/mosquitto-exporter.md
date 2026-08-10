# Mosquitto Exporter

Mosquitto $SYS metrics to OpenTelemetry/Prometheus exporter.

## Overview

The Mosquitto Exporter connects to a Mosquitto MQTT broker, subscribes to `$SYS/#` topics, parses the metrics, and exposes them via:
- Prometheus metrics endpoint (`/metrics`)
- OpenTelemetry OTLP (gRPC/HTTP)

## Metrics Collected

### Broker Information
| Metric | Type | Description |
|--------|------|-------------|
| `mosquitto_version` | Gauge | Mosquitto version string |
| `mosquitto_uptime_seconds` | Gauge | Broker uptime in seconds |
| `mosquitto_timestamp` | Gauge | Broker timestamp |

### Client Metrics
| Metric | Type | Description |
|--------|------|-------------|
| `mosquitto_clients_connected` | Gauge | Currently connected clients |
| `mosquitto_clients_disconnected_total` | Counter | Total disconnected clients |
| `mosquitto_clients_expired_total` | Counter | Expired sessions |
| `mosquitto_clients_maximum` | Gauge | Maximum concurrent clients |

### Message Metrics
| Metric | Type | Description |
|--------|------|-------------|
| `mosquitto_messages_received_total` | Counter | Total messages received |
| `mosquitto_messages_sent_total` | Counter | Total messages sent |
| `mosquitto_messages_dropped_total` | Counter | Total messages dropped |
| `mosquitto_messages_inflight` | Gauge | In-flight messages (QoS 1/2) |
| `mosquitto_messages_retained` | Gauge | Retained messages count |

### Message Rate Metrics (1/5/15 min averages)
| Metric | Type | Description |
|--------|------|-------------|
| `mosquitto_messages_received_1min` | Gauge | Messages received/sec (1m avg) |
| `mosquitto_messages_received_5min` | Gauge | Messages received/sec (5m avg) |
| `mosquitto_messages_received_15min` | Gauge | Messages received/sec (15m avg) |
| `mosquitto_messages_sent_1min` | Gauge | Messages sent/sec (1m avg) |
| `mosquitto_messages_sent_5min` | Gauge | Messages sent/sec (5m avg) |
| `mosquitto_messages_sent_15min` | Gauge | Messages sent/sec (15m avg) |

### Byte Metrics
| Metric | Type | Description |
|--------|------|-------------|
| `mosquitto_bytes_received_total` | Counter | Total bytes received |
| `mosquitto_bytes_sent_total` | Counter | Total bytes sent |
| `mosquitto_bytes_received_1min` | Gauge | Bytes received/sec (1m avg) |
| `mosquitto_bytes_received_5min` | Gauge | Bytes received/sec (5m avg) |
| `mosquitto_bytes_received_15min` | Gauge | Bytes received/sec (15m avg) |
| `mosquitto_bytes_sent_1min` | Gauge | Bytes sent/sec (1m avg) |
| `mosquitto_bytes_sent_5min` | Gauge | Bytes sent/sec (5m avg) |
| `mosquitto_bytes_sent_15min` | Gauge | Bytes sent/sec (15m avg) |

### Subscription Metrics
| Metric | Type | Description |
|--------|------|-------------|
| `mosquitto_subscriptions_count` | Gauge | Active subscriptions |
| `mosquitto_retained_messages_count` | Gauge | Retained messages |

### PUBLISH-Specific Metrics
| Metric | Type | Description |
|--------|------|-------------|
| `mosquitto_publish_received_total` | Counter | PUBLISH packets received |
| `mosquitto_publish_sent_total` | Counter | PUBLISH packets sent |
| `mosquitto_publish_bytes_received_total` | Counter | PUBLISH bytes received |
| `mosquitto_publish_bytes_sent_total` | Counter | PUBLISH bytes sent |
| `mosquitto_publish_dropped_total` | Counter | PUBLISH packets dropped |

## Configuration

### Environment Variables
| Variable | Default | Description |
|----------|---------|-------------|
| `MQTT_HOST` | `mosquitto` | Broker hostname |
| `MQTT_PORT` | `1883` | Broker port |
| `MQTT_USERNAME` | - | Username (optional) |
| `MQTT_PASSWORD` | - | Password (optional) |
| `MQTT_CLIENT_ID` | `mosquitto-exporter` | MQTT client ID |
| `MQTT_VERSION` | `5` | MQTT protocol version (3 or 5) |
| `MQTT_KEEPALIVE` | `60` | Keepalive interval |
| `MQTT_TLS_ENABLED` | `false` | Enable TLS |
| `MQTT_TLS_CA_CERT` | - | CA certificate path |
| `MQTT_TLS_CERTFILE` | - | Client certificate path |
| `MQTT_TLS_KEYFILE` | - | Client key path |

### Prometheus
| Variable | Default | Description |
|----------|---------|-------------|
| `PROMETHEUS_ENABLED` | `true` | Enable Prometheus endpoint |
| `PROMETHEUS_PORT` | `9494` | Prometheus metrics port |
| `PROMETHEUS_PATH` | `/metrics` | Metrics endpoint path |

### OpenTelemetry
| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otelcol:4317` | OTLP endpoint |
| `OTEL_SERVICE_NAME` | `mosquitto-exporter` | Service name |
| `OTEL_EXPORTER_OTLP_INSECURE` | `true` | Use insecure connection |
| `OTEL_EXPORTER_OTLP_TIMEOUT` | `10` | Export timeout (seconds) |
| `OTEL_METRIC_EXPORT_INTERVAL` | `30000` | Export interval (ms) |

### Metrics Collection
| Variable | Default | Description |
|----------|---------|-------------|
| `METRICS_SCRAPE_INTERVAL` | `30` | Scrape interval (seconds) |
| `METRICS_STALE_THRESHOLD` | `120` | Stale metric threshold (seconds) |

## Deployment

### Docker Compose (Recommended)

```yaml
services:
  mosquitto-exporter:
    build:
      context: ../mosquitto-exporter
      dockerfile: Dockerfile
    container_name: mosquitto-exporter
    ports:
      - "9494:9494"
    environment:
      - MQTT_HOST=mosquitto
      - MQTT_PORT=1883
      - PROMETHEUS_ENABLED=true
      - PROMETHEUS_PORT=9494
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://otelcol:4317
      - OTEL_SERVICE_NAME=mosquitto-exporter
    depends_on:
      mosquitto:
        condition: service_healthy
      otelcol:
        condition: service_started
    networks:
      - mqtt-observability
```

### Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mosquitto-exporter
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mosquitto-exporter
  template:
    metadata:
      labels:
        app: mosquitto-exporter
    spec:
      containers:
      - name: exporter
        image: your-registry/mosquitto-exporter:latest
        ports:
        - containerPort: 9494
        env:
        - name: MQTT_HOST
          value: "mosquitto"
        - name: OTEL_EXPORTER_OTLP_ENDPOINT
          value: "http://otelcol:4317"
---
apiVersion: v1
kind: Service
metadata:
  name: mosquitto-exporter
spec:
  selector:
    app: mosquitto-exporter
  ports:
  - port: 9494
    targetPort: 9494
```

## Prometheus Configuration

```yaml
scrape_configs:
  - job_name: 'mosquitto-exporter'
    static_configs:
      - targets: ['mosquitto-exporter:9494']
    metrics_path: '/metrics'
    scrape_interval: 10s
```

## Dashboard Queries

### Connected Clients
```promql
mosquitto_clients_connected
```

### Message Rate (per second)
```promql
rate(mosquitto_messages_received_total[1m])
rate(mosquitto_messages_sent_total[1m])
rate(mosquitto_messages_dropped_total[1m])
```

### Throughput
```promql
rate(mosquitto_bytes_received_total[1m])
rate(mosquitto_bytes_sent_total[1m])
```

### Client Utilization
```promql
mosquitto_clients_connected / mosquitto_clients_maximum * 100
```

### In-Flight Messages
```promql
mosquitto_messages_inflight
```

### Subscription Count
```promql
mosquitto_subscriptions_count
```

## Testing

```bash
# Start exporter
docker compose -f docker/docker-compose.yml up -d mosquitto-exporter

# Check metrics
curl http://localhost:9494/metrics

# Verify $SYS topics in broker
mosquitto_sub -h localhost -p 1883 -t '$SYS/#' -v
```

## Requirements

- Mosquitto with `$SYS` topics enabled (`sys_interval` > 0 in config)
- MQTT v3.1.1 or v5 broker
- Network access to broker port (default 1883)

## Mosquitto Configuration

Ensure your `mosquitto.conf` includes:

```conf
# Enable $SYS topics (required!)
sys_interval 10
sys_topic_enabled true

# Optional: Persist $SYS for restarts
persistence true
persistence_location /mosquitto/data/
```