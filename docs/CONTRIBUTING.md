# Contributing to MQTT Observability OpenTelemetry

Thank you for your interest in contributing! This guide covers the development workflow.

## Development Setup

### Prerequisites
- Python 3.11+
- Docker & Docker Compose
- Git

### Clone and Install

```bash
git clone https://github.com/4alvit/mqtt-observability-opentelemetry
cd mqtt-observability-opentelemetry

# Install dependencies for each component
cd mqtt-interceptor && pip install -e ".[dev]" && cd ..
cd mosquitto-exporter && pip install -e ".[dev]" && cd ..
```

### Run Tests

```bash
# All components
cd mqtt-interceptor && pytest -v && cd ..
cd mosquitto-exporter && pytest -v && cd ..
```

### Run Linters

```bash
# Ruff (fast Python linter)
ruff check mqtt-interceptor/src mosquitto-exporter/src
ruff format mqtt-interceptor/src mosquitto-exporter/src

# MyPy (type checking)
mypy mqtt-interceptor/src mosquitto-exporter/src
```

## Project Structure

```
mqtt-observability-opentelemetry/
├── mqtt-interceptor/          # MQTT proxy with trace propagation
│   ├── src/mqtt_interceptor/
│   │   ├── __main__.py        # Entry point
│   │   ├── config.py          # Configuration
│   │   └── propagator.py      # Trace context handling
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
│
├── mosquitto-exporter/        # $SYS metrics to OTel/Prometheus
│   ├── src/mosquitto_exporter/
│   │   ├── __main__.py
│   │   ├── config.py
│   │   ├── collector.py       # MQTT subscriber + metrics
│   │   └── metrics.py         # Prometheus/OTel metrics
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
│
├── topic-span-processor/      # (Future) OTel span processor
│
├── grafana-dashboards/        # Pre-built dashboards
│   ├── mqtt-broker-overview.json
│   ├── mqtt-tracing.json
│   └── mqtt-topic-analysis.json
│
├── docker/                    # Docker Compose stack
│   ├── docker-compose.yml
│   ├── mosquitto.conf
│   ├── otelcol-config.yaml
│   ├── prometheus.yml
│   ├── grafana/
│   ├── mqtt-interceptor/Dockerfile
│   ├── mosquitto-exporter/Dockerfile
│   └── test-publisher/
│
└── docs/                      # Documentation
```

## Component Development

### MQTT Interceptor

Key files:
- `src/mqtt_interceptor/__main__.py` - Main entry, async MQTT proxy
- `src/mqtt_interceptor/config.py` - Pydantic settings
- `src/mqtt_interceptor/propagator.py` - W3C trace context extraction/injection

Run locally:
```bash
cd mqtt-interceptor
MQTT_UPSTREAM_HOST=localhost python -m mqtt_interceptor
```

### Mosquitto Exporter

Key files:
- `src/mosquitto_exporter/__main__.py` - Entry point
- `src/mosquitto_exporter/collector.py` - SYS topic subscriber + parser
- `src/mosquitto_exporter/metrics.py` - Metric definitions

Run locally:
```bash
cd mosquitto-exporter
MQTT_HOST=localhost python -m mosquitto_exporter
```

## Adding a New Metric

1. Add metric definition in `metrics.py` (exporter) or `__main__.py` (interceptor)
2. Update parsing logic in `collector.py` or message handler
3. Add to dashboard JSON in `grafana-dashboards/`
4. Update README.md metric tables

## Adding a New Dashboard

1. Create JSON in `grafana-dashboards/`
2. Follow naming: `mqtt-<feature>.json`
3. Use datasource UID references (not names)
4. Add to `docker/grafana/provisioning/dashboards/dashboards.yml`
5. Test with `docker compose up grafana`

## Testing with Demo Stack

```bash
cd docker
docker compose up -d

# Check services
docker compose ps

# View logs
docker compose logs -f mqtt-interceptor
docker compose logs -f mosquitto-exporter

# Clean up
docker compose down -v
```

Access points:
- Grafana: http://localhost:3000 (admin/admin)
- Jaeger: http://localhost:16686
- Prometheus: http://localhost:9090
- Mosquitto: localhost:1883
- Interceptor: localhost:1884

## Pull Request Process

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Make your changes
4. Run tests and linters
5. Commit with conventional commit format
6. Push and open a PR

### Commit Message Format

```
<type>(<scope>): <subject>

<body>

<footer>
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

Example:
```
feat(interceptor): add MQTT v3 topic-based trace propagation

Add fallback trace context encoding in topic prefix for v3.1.1 brokers
that don't support user properties.

Closes #42
```

## Code Style

- **Python**: Ruff (line-length 100, Python 3.11+)
- **Type hints**: Required (mypy strict)
- **Docstrings**: Google style for public APIs
- **Logging**: Structured JSON via structlog

## Releasing

Releases are automated via GitHub Actions on tag push:

```bash
git tag v0.1.0
git push origin v0.1.0
```

This triggers:
1. Build & test all components
2. Build Docker images
3. Push to GHCR
4. Create GitHub Release

## Questions?

Open an issue or start a discussion on GitHub.