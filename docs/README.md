# Documentation index

These documents describe the MQTT observer, the Mosquitto exporter and their
OpenTelemetry/OTLP transport, recovery and evidence.

## Get started

| Document | Description |
| --- | --- |
| [MQTT observer](mqtt-interceptor.md) | The passive `mqtt-interceptor` subscriber and what it records. |
| [Mosquitto Exporter](mosquitto-exporter.md) | Exporting Mosquitto `$SYS` metrics to OpenTelemetry and Prometheus. |
| [Contributing to MQTT Observability OpenTelemetry](CONTRIBUTING.md) | Development workflow and contribution expectations. |

## Configure transport

| Document | Description |
| --- | --- |
| [OTLP transport and certificate keys](otlp-transport.md) | TLS-protected trace/metric export and certificate requirements. |
| [Kubernetes recovery TLS boundary](recovery-tls.md) | How the recovery bearer token is protected in transit. |
| [Release helper TLS profile](RELEASE_TLS_PROFILE.md) | Verified TLS profile used by release helpers. |

## Operate and release

| Document | Description |
| --- | --- |
| [Venus metrics in the Kubernetes Prometheus](venus-scraping.md) | Scraping Venus metrics into the Prometheus endpoint. |
| [Dashboard screenshot](screenshots/README.md) | Source of the Grafana panel layout and how the image is captured. |
| [Release-candidate fuzz testing](fuzzing.md) | The bounded RC fuzz workflow and its release-inventory binding. |
| [Release version inputs](release-versioning.md) | The `versioning` fields that identify a release. |
| [CI orchestration](CI-ORCHESTRATION.md) | How the quality gate and release pipeline divide validation. |

## Assurance

| Document | Description |
| --- | --- |
| [OpenSSF Best Practices evidence](openssf-evidence.md) | Evidence index for the Passing self-assessment. |

Shared repository policy: [release-workflow.md](release-workflow.md), [runner-preflight.md](runner-preflight.md), [release-read-safety.md](release-read-safety.md), [VERSIONING.md](VERSIONING.md) and [rc-fuzzing.md](rc-fuzzing.md) are generated or shared boilerplate maintained in [victron-venus/venus-os-ci-toolkit](https://github.com/victron-venus/venus-os-ci-toolkit).
