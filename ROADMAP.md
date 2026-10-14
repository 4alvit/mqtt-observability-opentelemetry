# Roadmap: October 2026 to October 2027

The project prioritizes reliable, reviewable MQTT tracing, broker metrics and the accompanying observability and recovery components. This roadmap describes intended maintenance work, not delivery promises.

1. Keep dependencies, pinned build tools and supported runtimes current; retain deterministic input and release provenance checks.
2. Extend negative tests at documented trust boundaries and preserve regression tests for fixed defects. Record fuzz targets and coverage with their exact source revision and limitations.
3. Keep installation, configuration, upgrade and security guidance consistent with the source; make failures actionable without exposing credentials or household data.
4. Improve complete-source test measurement, release verification instructions and maintenance continuity. The missing backup/recovery arrangement remains an explicit limitation until implemented and tested.

Out of scope are claims of universal hardware compatibility, unattended production changes based only on CI, safety-critical certification, and implied support for external software outside this repository. Significant changes to supported interfaces or deployment requirements will be discussed in public issues and documented in release notes.
