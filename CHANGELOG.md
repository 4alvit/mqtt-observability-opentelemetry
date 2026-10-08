# Changelog

## [0.2.3]

### Changed

Document the MQTT interceptor, exporter and trace-propagation boundaries together with contributor checks and vulnerability reporting. Releases carry reviewed human change notes alongside immutable build provenance.

### Upgrade

The interceptor/exporter configuration and message formats are unchanged by this documentation and release-tooling update. Preserve deployment secrets outside source archives and use the isolated Compose smoke test before applying deployment changes.

### Security

No application vulnerability is claimed fixed by this documentation update. Release publication now validates the source changelog and rejects missing upgrade/security guidance before tagging.

