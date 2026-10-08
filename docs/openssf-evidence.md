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

## Criteria still requiring verification

Before submitting or updating the questionnaire, verify the actual project-specific record: responses to bug and enhancement reports, vulnerability reports in every supported channel, release-note history, unresolved scanner findings, dependency status and required review settings. The primary maintainer must personally confirm knowledge of secure design and common implementation vulnerabilities. A confirmation about another repository does not establish these answers here.

Assess transport encryption, credential storage and privilege limits against the implementation and deployment documented in [SECURITY.md](../SECURITY.md). Do not mark a requirement satisfied solely because a policy says it should be. Record justified non-applicability only where the actual architecture supports it. No paid certification, blanket compliance guarantee or third-party audit is claimed.


### Release-note structure

Source release notes use ATX headings: `## [version]`, `### Upgrade`, and
`### Security`. A heading at the same or a higher level ends its section.
Setext (underlined) headings within the selected release are rejected because
this deliberately limited parser does not implement all CommonMark structure.
Fenced examples and HTML comments cannot supply the required guidance; normal
blank-separated thematic breaks remain supported. The published release body
preserves the original source text after validation.
