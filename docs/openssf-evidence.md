# OpenSSF Best Practices evidence

This is an evidence index for the OpenSSF Best Practices Passing self-assessment. It is not an assertion that a badge has been awarded or that every criterion is satisfied. The public badge service is the authority for an awarded status.

## Project and participation

MQTT tracing and broker metrics components, plus a local OpenTelemetry observability stack.

The project is developed publicly in [Git](https://github.com/4alvit/mqtt-observability-opentelemetry) under the [MIT license](../LICENSE). Its source, issue tracker and pull requests are available without a paid account. [Contribution instructions](../CONTRIBUTING.md) describe reporting, changes, coding conventions, tests and review. The [security policy](../SECURITY.md) provides a confidential vulnerability-reporting path, support scope, response targets and deployment boundaries.

## User and interface documentation

- [`README.md`](../README.md)
- [`docs/mqtt-interceptor.md`](../docs/mqtt-interceptor.md)
- [`docs/mosquitto-exporter.md`](../docs/mosquitto-exporter.md)
- [`docs/CONTRIBUTING.md`](../docs/CONTRIBUTING.md)
- [`RELEASING.md`](../RELEASING.md)

## Source, testing and analysis

- [`mqtt-interceptor/src`](../mqtt-interceptor/src)
- [`mosquitto-exporter/src`](../mosquitto-exporter/src)
- [`docker`](../docker)

- [Test suite](../tests) and [CI workflows](../.github/workflows)
- [Local CI entry point](../scripts/ci.sh)
- [CodeQL analysis](../.github/workflows/codeql.yml)
- [Dependency update configuration](../.github/dependabot.yml)

The component gates use each component’s uv lock, Ruff, mypy and pytest with coverage; recovery and deployment policy tests are also included. Run `bash scripts/ci.sh integration` with Docker for the disposable Compose smoke test. Kubernetes/host monitoring checks require the explicit fixtures documented by their scripts.

CI results are evidence for the tested revision and environment, not proof of safe production or hardware operation. Check the current default-branch runs and unresolved security findings before answering the analysis criteria. Fuzzing, coverage completeness and independent penetration testing must be supported by actual runs; ordinary unit tests must not be presented as those activities.

## Changes and releases

Follow `RELEASING.md` and `docs/release-versioning.md`. Document component/image versions, configuration and telemetry-schema migrations, and security fixes without publishing live telemetry. The [release policy](../.release-policy.json) records automation behavior. A new release must identify its source revision and explain notable changes; security fixes must identify relevant advisories when known.

## Assessment evidence and limits

The maintainer confirmed knowledge of secure design and common implementation
vulnerabilities for this project. On 2026-10-14 the inspected GitHub history had
no ordinary bug/enhancement issues or security advisories; the maintainer also
confirmed no external reports during the preceding 12 months. Private GitHub
vulnerability reporting is enabled. These historical checks are dated evidence,
not a promise that future reports can be ignored.

The source-bound `v0.2.5-rc.2` release report records a successful 60-second
`trace-config` fuzz campaign at commit
`a414aa2bd8cfbf6f1f6c0871ae814869ee0c5017`:
[run 38491877975](https://github.com/4alvit/mqtt-observability-opentelemetry/actions/runs/38491877975).
This is scoped Python fuzzing; it does not establish complete input coverage or
an independent penetration test. The newer MQTT TLS change adds real loopback
handshake regressions to the existing TLS job; those regressions are separate
from the earlier fuzz campaign.

[Governance](../GOVERNANCE.md), [conduct](../CODE_OF_CONDUCT.md) and the
[roadmap](../ROADMAP.md) describe project participation and its limits. There is
no backup maintainer or tested one-week access recovery process. Silver
continuity, whole-project coverage and independent security-assurance claims
remain unmet. The dynamic README badge links to the public assessment; the
badge service alone determines its awarded level.


### Release-note structure

Source release notes use ATX headings: `## [version]`, `### Upgrade`, and
`### Security`. A heading at the same or a higher level ends its section.
Setext (underlined) headings within the selected release are rejected because
this deliberately limited parser does not implement all CommonMark structure.
Fenced examples and HTML comments cannot supply the required guidance; normal
blank-separated thematic breaks remain supported. The published release body
preserves the original source text after validation.
