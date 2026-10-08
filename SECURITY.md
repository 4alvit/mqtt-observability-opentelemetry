# Security Policy

## Reporting a Vulnerability

Private vulnerability reporting is enabled for this repository. Use
[Report a vulnerability](https://github.com/4alvit/mqtt-observability-opentelemetry/security/advisories/new)
to send a confidential report to the maintainers. Follow
[GitHub's private reporting instructions](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
if you need help submitting the report.

Include the affected version or commit, steps to reproduce, expected and actual
behavior, and potential impact. Remove access tokens, credentials and personal
data from examples. Do not disclose exploit details in public issues before
coordinating with the maintainers.

## Support and response

Security fixes target the current default branch and the latest maintained release, where releases exist. Older versions are not promised backports. Maintainers aim to acknowledge private reports within 14 days, investigate and communicate status within 60 days, and coordinate disclosure with the reporter. Confirmed vulnerabilities with a practical fix receive priority over feature work; publish an advisory and release notes that identify affected versions, mitigation and the fixed version. If a fix takes longer, keep the reporter informed without exposing confidential details.

## Deployment trust boundaries

MQTT payloads, topic names and propagated trace context are untrusted and may contain personal data or secrets. Bound parsing and avoid capturing credentials in attributes, logs or metrics labels. Protect exporters, dashboards, collectors and broker ports from public access. Authenticate/encrypt traffic across untrusted networks and review retention before collecting household telemetry. Host monitoring and recovery helpers need separate privilege review.

Use synthetic data for testing. Never attach live tokens, private keys, database exports or household telemetry to public CI artifacts. Report a suspected credential exposure privately and revoke the credential through its issuer. See [CONTRIBUTING.md](CONTRIBUTING.md) for validation and [the evidence index](docs/openssf-evidence.md) for assessment limits.
