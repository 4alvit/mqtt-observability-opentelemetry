# MQTT Observability with OpenTelemetry

[![CI](https://github.com/4alvit/mqtt-observability-opentelemetry/actions/workflows/ci.yml/badge.svg)](https://github.com/4alvit/mqtt-observability-opentelemetry/actions)
[![License](https://img.shields.io/github/license/4alvit/mqtt-observability-opentelemetry)](https://github.com/4alvit/mqtt-observability-opentelemetry/blob/main/LICENSE)
[![codecov](https://img.shields.io/codecov/c/github/4alvit/mqtt-observability-opentelemetry)](https://app.codecov.io/gh/4alvit/mqtt-observability-opentelemetry)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)

---

⭐ **If this project helps you, please star it!** Stars help others discover it and motivate continued development.

---

A complete observability stack for MQTT-based IoT systems.

## Quick Start

```bash
# Clone and start the demo stack
cd docker
docker compose up -d

# Verify services
docker compose ps

# Access dashboards
open http://localhost:3000  # Grafana (admin/admin)
open http://localhost:16686 # Jaeger
```

## Architecture

```mermaid
graph TD
    Devices[📱 Devices] -->|MQTT 1883| Mosquitto[🦟 Mosquitto Broker]

    Mosquitto -->|$SYS/# + #| Interceptor[🔍 MQTT Interceptor :1884]
    Mosquitto -->|$SYS/#| Exporter[📊 Mosquitto Exporter :9494]
    Mosquitto -->|#| SpanProc[🔗 Topic Span Processor]

    Interceptor -->|OTLP Traces| Collector[🔄 OTel Collector]
    Exporter -->|OTLP Metrics| Collector
    SpanProc -->|OTLP Spans| Collector

    Collector -->|Traces| Jaeger[🔭 Jaeger :16686]
    Collector -->|Metrics| Prometheus[📈 Prometheus :9090]

    Prometheus --> Grafana[📊 Grafana :3000]
    Jaeger --> Grafana
```

## Components

### 1. MQTT Interceptor (`mqtt-interceptor/`)
- **Port**: 1884 (proxies to 1883)
- **Function**: Intercepts MQTT messages, extracts/injects W3C Trace Context
- **Features**: Topic-based span creation, configurable sampling, OTLP export

### 2. Mosquitto Exporter (`mosquitto-exporter/`)
- **Port**: 9494 (Prometheus), OTLP to collector
- **Function**: Scrapes `$SYS/#` topics, exports broker metrics
- **Metrics**: 40+ metrics covering clients, messages, bytes, subscriptions

### 3. Topic Span Processor (`topic-span-processor/`)
- Creates spans based on MQTT topic patterns
- Extracts attributes from topic segments
- Correlates parent/child spans via trace context

### 4. Grafana Dashboards (`grafana-dashboards/`)
| Dashboard | Description |
|-----------|-------------|
| MQTT Broker Overview | Connections, message rates, throughput |
| MQTT Distributed Tracing | Latency, errors, trace visualization |
| MQTT Topic Analysis | Per-topic metrics, patterns |

### 5. Docker Compose Stack (`docker/`)
Complete demo environment with:
- Mosquitto broker
- MQTT Interceptor
- Mosquitto Exporter
- OTEL Collector
- Prometheus
- Grafana
- Jaeger
- Test publisher

## Data Flow

```mermaid
sequenceDiagram
    participant D as Device
    participant I as Interceptor
    participant B as Broker
    participant E as Exporter
    participant C as OTel Collector
    participant J as Jaeger
    participant P as Prometheus
    participant G as Grafana

    D->>I: PUBLISH (with traceparent)
    I->>I: Extract trace context
    I->>I: Create span for topic match
    I->>B: Forward PUBLISH (with trace context)

    B->>E: $SYS/# topics
    E->>C: OTLP Metrics

    C->>J: Traces
    C->>P: Metrics

    P->>G: Query metrics
    J->>G: Query traces

    G->>User: Dashboards
```

## Configuration

All components configure via environment variables or YAML config files. See component docs:

- [MQTT Interceptor](docs/mqtt-interceptor.md)
- [Mosquitto Exporter](docs/mosquitto-exporter.md)
- [Docker Compose](docs/docker-compose.md)
- [Grafana Dashboards](docs/grafana-dashboards.md)

## Trace Context Propagation

### MQTT v5 (User Properties)
```python
# Inject trace context
props = mqtt.Properties(PacketTypes.PUBLISH)
props.UserProperty = [
    ("traceparent", "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"),
    ("tracestate", "congo=t61rcWkgMzE")
]
client.publish("devices/sensor-001/telemetry", payload, properties=props)

# Extract trace context
for k, v in msg.properties.UserProperty:
    if k == "traceparent":
        traceparent = v
```

### MQTT v3.1.1 (Topic-based)
Uses topic pattern `trace/<trace_id>/<span_id>` for correlation.

## Example: Publishing with Traces

```bash
# With trace context (MQTT v5)
mosquitto_pub -h localhost -p 1884 \
  -t 'devices/sensor-001/telemetry' \
  -m '{"temperature": 23.5}' \
  -q 1 -V 5 \
  -D '{"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"}'

# View traces in Jaeger
open http://localhost:16686
```

## Developing

```bash
# Install dependencies
pip install -e mqtt-interceptor[dev]
pip install -e mosquitto-exporter[dev]

# Run tests
pytest mqtt-interceptor/tests/
pytest mosquitto-exporter/tests/

# Lint
ruff check mqtt-interceptor/ mosquitto-exporter/
mypy mqtt-interceptor/src/ mosquitto-exporter/src/
```

## Deploying to Production

### Kubernetes (Helm)
```bash
# Coming soon: Helm charts for each component
helm install mqtt-obs ./helm/chart
```

### Key Production Considerations
1. **TLS**: Enable TLS for all MQTT and OTLP connections
2. **Authentication**: Configure username/password or certificates
3. **Resource Limits**: Set CPU/memory limits for containers
4. **Persistence**: Use persistent volumes for Prometheus/Grafana/Jaeger
5. **Sampling**: Adjust trace sampling rate (default 10%) based on volume
6. **Retention**: Configure Prometheus/Jaeger retention policies

## License

MIT License - see [LICENSE](LICENSE)

---

## Related Projects

| Project | Scope | When to Use |
|---------|-------|-------------|
| **mqtt-observability-opentelemetry** (this) | **Generic** — Works with ANY MQTT broker. No Venus OS dependency. | Generic MQTT/IoT observability, any broker, any device types |
| [venus-os-observability](https://github.com/victron-venus/venus-os-observability) | **Venus OS specific** — Depends on D-Bus, Victron protocols. | Victron Venus OS only: D-Bus event tracing, inverter metrics, Cerbo GX integration |

**Choose this repo if:** You need MQTT observability for any IoT system (industrial, home automation, custom devices).

**Choose venus-os-observability if:** You are running Victron Venus OS (Cerbo GX, Raspberry Pi with Venus OS) and need D-Bus integration, inverter-specific metrics, and Venus OS native deployment.
