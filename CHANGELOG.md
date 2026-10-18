# Changelog

## [0.2.8] - 2026-10-18

### Changed

- Refresh the synthetic Grafana dashboard preview and pinned review, merge and RC fuzzing workflows.

### Upgrade

Preserve installation-specific configuration and retain the previous immutable release for rollback. Required CI and source-bound RC qualification remain enabled. Publishing this release does not deploy it to an installation or establish hardware acceptance.

### Security

Broker credentials, collector TLS verification and persistent monitoring data remain protected by the existing configuration. No new runtime security fix is claimed.

## [0.2.7] - 2026-10-17

### Changed

- Clarify where RC fuzz safeguards execute and add illustrated process and result guides.
- Keep the interceptor and exporter version metadata synchronized for this release cycle.

### Upgrade

Runtime behavior and configuration remain unchanged from 0.2.6. Preserve broker credentials, collector endpoints and persistent monitoring data; retain the previously accepted artifacts for rollback.

### Security

No new runtime security fix is claimed. Existing TLS checks and source-bound RC fuzzing remain required; publication does not change any running monitoring deployment.

## [0.2.6] - 2026-10-14

### Changed

- Validate each public release candidate with bounded, network-isolated fuzz campaigns against its exact source revision.
- Require positive engine execution counts, instrumented production code and validated seed corpora; retain source-bound reports and diagnostic evidence.
- Run deterministic seed and configuration checks in ordinary CI without starting mutation campaigns on betas.
- Distinguish expected TLS-rejection connection closure from truncated CONNECT requests in the synthetic proxy test fixture; retain the no-payload-on-rejection assertions.

### Upgrade

Use the existing installation procedure, preserve local configuration and keep the previous accepted package for rollback. This maintenance release updates release validation and does not change the application configuration format. Deployment and applicable device or integration acceptance remain separate from publication.

### Security

RC publication rejects absent or invalid source-bound fuzz reports. The fuzz runner bounds corpus paths, file types and size. No application vulnerability or new target-device acceptance is claimed by these tooling changes.

## [0.2.5]

### Release candidate validation

- Require bounded fuzzing on public release candidates, with source-bound reports and retained diagnostics; beta and ordinary CI do not run mutation campaigns.

### Changed

Require exact verified-chain key minima for MQTT broker TLS before transmitting credentials or payloads.

Allow bounded Prometheus WAL recovery before liveness checks begin and align
the rollout deadline with that startup budget. Retain the Grafana probe fixes
for shared CPU load. Both Python components now use the same 0.2.5 base version.

### Upgrade

Replace MQTT broker/client certificate chains containing RSA keys below 2048 bits or EC keys below 224 bits. TLS peers must pass both normal trust/hostname validation and exact key checks; plaintext local operation is unchanged.

Preserve existing Prometheus storage, collector endpoints and credentials.
The Prometheus manifest allows up to thirty minutes for startup; readiness
continues to gate traffic. Publishing a candidate does not apply manifests or
change the running recovery image.

### Security

The recovery client now validates Kubernetes certificate key strength on the
same TLS connection before sending its bearer token. Release publication
requires an authoritative GitHub HTTP status before treating a tag as absent.
Certificate trust and hostname checks remain required.

## [0.2.4]

### Changed

Add a hash-locked recovery runtime image to the existing verified multi-platform
release artifacts. Its default user and group are unprivileged ID 65532. The
existing Kubernetes recovery deployment is unchanged until a separately
reviewed change pins a published immutable image digest.

### Upgrade

For encrypted trace or metric export, explicitly select `http/protobuf` and an
`https://` collector endpoint. HTTPS/gRPC export now fails with a migration
message; explicitly configured local plaintext gRPC remains available. Keep
signal-specific endpoint paths when using the standard OTLP environment
variables, and see [the migration guide](docs/otlp-transport.md) for CA, proxy,
client-certificate and metrics settings. Existing trace/metric payloads and the
Prometheus reader remain unchanged. Do not replace the recovery CronJob image
with an unverified tag or infer that this release changes Kubernetes API TLS.

### Security

Release tooling now requires an authoritative HTTP 404 response before treating
a GitHub resource as absent. Status-shaped error messages and unavailable
response statuses fail closed before release publication or version allocation.

The owned HTTPS trace and metric exporters verify exact certificate-key minima
on the same connection before sending credentials or telemetry, including
proxy chains and configured client certificates. RSA keys below 2048 bits and
EC keys below 224 bits are rejected even when the TLS provider's rounded key
classification would accept them. Certificate trust and hostname validation
remain required. This release does not claim coverage of the still-unchanged
Kubernetes recovery transport or operator-managed inbound TLS endpoints.

## [0.2.3]

### Changed

Document the MQTT interceptor, exporter and trace-propagation boundaries together with contributor checks and vulnerability reporting. Releases carry reviewed human change notes alongside immutable build provenance.

### Upgrade

The interceptor/exporter configuration and message formats are unchanged by this documentation and release-tooling update. Preserve deployment secrets outside source archives and use the isolated Compose smoke test before applying deployment changes.

### Security

No application vulnerability is claimed fixed by this documentation update. Release publication now validates the source changelog and rejects missing upgrade/security guidance before tagging.
