# Contributing

MQTT tracing and broker metrics components, plus a local OpenTelemetry observability stack.

## Questions, bugs and proposals

Use [GitHub Issues](https://github.com/4alvit/mqtt-observability-opentelemetry/issues) for questions, bug reports and feature proposals. Search existing issues first. Describe the affected version/commit, expected and actual behavior, minimal reproduction and relevant environment. Remove tokens, private endpoints, household identifiers and personal data from examples. Security vulnerabilities use the confidential process in [SECURITY.md](SECURITY.md).

Anyone may propose a change through a pull request. Discuss compatibility or architectural changes in an issue before a large implementation. Maintainers aim to acknowledge actionable reports within 14 days; security reports follow the security policy. No paid support or response-time guarantee is implied.

## Development and validation

Clone the repository, create a branch from `main`, and use the Python version and dependencies declared by the project and CI. Run from the repository root:

```sh
bash scripts/ci.sh lint
bash scripts/ci.sh test
```

The component gates use each component’s uv lock, Ruff, mypy and pytest with coverage; recovery and deployment policy tests are also included. Run `bash scripts/ci.sh integration` with Docker for the disposable Compose smoke test. Kubernetes/host monitoring checks require the explicit fixtures documented by their scripts.

For a bug fix, add a regression test that fails before the fix and passes afterward. For new functionality, test normal behavior, invalid input and relevant authorization/error paths. Preserve existing checks; do not lower coverage gates or ignore findings merely to obtain a green build. Python code follows the configured formatter/linter where present and normal PEP 8 conventions otherwise. Keep shell, YAML and generated examples compatible with their declared tools.

## Review and compatibility

Keep pull requests focused and explain the problem, resulting behavior, compatibility impact and exact validation performed. Update the user-facing documentation when changing configuration, interfaces or operational behavior. Call out tests not run and their prerequisites. Maintainers review changes through GitHub pull requests and required CI; automated review is supplemental. Contributions are provided under the repository's [MIT license](LICENSE); a contributor must have the right to submit the work.

Follow `RELEASING.md` and `docs/release-versioning.md`. Document component/image versions, configuration and telemetry-schema migrations, and security fixes without publishing live telemetry.

## Source and interfaces

- [`mqtt-interceptor/src`](mqtt-interceptor/src)
- [`mosquitto-exporter/src`](mosquitto-exporter/src)
- [`docker`](docker)
- [`README.md`](README.md)
- [`docs/mqtt-interceptor.md`](docs/mqtt-interceptor.md)
- [`docs/mosquitto-exporter.md`](docs/mosquitto-exporter.md)
- [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md)
- [`RELEASING.md`](RELEASING.md)

See the [OpenSSF evidence index](docs/openssf-evidence.md) for the current assessment scope and outstanding verification.

Release changes must update `CHANGELOG.md` under a unique `## [X.Y.Z]`
base-version heading, including nonempty `### Upgrade` and `### Security`
sections. The release controller reads that file from the exact packaged source
commit, validates its Git blob, and retains build provenance in the public notes.
Run `python3 -m unittest discover -s .github/release-tests -p "test_*.py"`
after changing release tooling.
