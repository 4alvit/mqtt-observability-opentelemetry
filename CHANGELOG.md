# Changelog

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
