# MQTT Observability with OpenTelemetry

[![CI](https://github.com/4alvit/mqtt-observability-opentelemetry/actions/workflows/ci.yml/badge.svg)](https://github.com/4alvit/mqtt-observability-opentelemetry/actions)
[![License](https://img.shields.io/github/license/4alvit/mqtt-observability-opentelemetry)](https://github.com/4alvit/mqtt-observability-opentelemetry/blob/main/LICENSE)
[![codecov](https://img.shields.io/codecov/c/github/4alvit/mqtt-observability-opentelemetry)](https://app.codecov.io/gh/4alvit/mqtt-observability-opentelemetry)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)

---

⭐ **If this project helps you, please star it!** Stars help others discover it and motivate continued development.

---

A complete observability stack for MQTT-based IoT systems.

<!-- ci-release-process:start -->
## Release process

See the [release strategy](RELEASING.md) for validation, nightly, beta, RC and stable promotion rules, and the [operator runbook](docs/release-workflow.md) for local commands.
<!-- ci-release-process:end -->

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

## Runtime architecture

The MQTT observer (`mqtt-interceptor`) subscribes to selected topics on an
existing broker and exports sampled consumer spans to an OpenTelemetry
collector. It exposes Prometheus metrics on port 9464; it is not a TCP proxy
and does not forward or modify device messages.

The `mosquitto-exporter` subscribes to fixed `$SYS/broker/...` statistics topics,
exports absolute broker totals and gauges to Prometheus on port 9494, and can
also send OTLP metrics to the collector. Mosquitto-compatible broker statistics
must be available; absent metrics are not fabricated.

The collector forwards traces to the configured backend and exposes metrics to
Prometheus. Grafana uses the existing Prometheus and trace-backend data sources.
The Docker Compose demo has its own disposable broker and Jaeger. The k3s base
contains the existing Prometheus/Tempo/Grafana stack; MQTT components require
an explicit deployment.

## Configuration

See [MQTT observer](docs/mqtt-interceptor.md),
[broker exporter](docs/mosquitto-exporter.md), and
[k3s monitoring](deploy/k3s/README.md). Use `MQTT_UPSTREAM_HOST`,
`MQTT_UPSTREAM_PORT`, and `OTEL_ENDPOINT` for component endpoints.

Sampling limits exported spans, not subscribed MQTT traffic. Select narrow
filters; the observer never exports message payloads. W3C context received in
MQTT 5 user properties becomes the consumer span's remote parent.

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


### Reviewed infrastructure deployment

The k3s observability stack uses third-party pinned images and configuration from this repository; it is separate from the MQTT component image releases. Deploy it with the manual `Deploy k3s` workflow and the exact default-branch `source_sha`, after the CI gate and production-environment approval. Locally, `SOURCE_SHA=<full SHA> bash deploy/k3s/deploy.sh --render` renders without applying; add `--apply` only for the reviewed committed configuration. Failed rollouts now fail the command.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for development, bug reports and proposals,
[SECURITY.md](SECURITY.md) for confidential vulnerability reports and deployment
boundaries, and the [OpenSSF evidence index](docs/openssf-evidence.md) for assessment
scope and verification.
