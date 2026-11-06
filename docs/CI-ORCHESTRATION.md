# CI orchestration

Quality gate owns pull request and merge queue validation. The release pipeline
owns default branch and nightly checks for applications; validation-only projects
run those events through Quality gate. Callable validators do not launch duplicate
runs. Superseded PR runs are cancelled, and validation jobs have explicit timeouts.

The required CI configuration contracts check CodeQL pin compatibility, validation
triggers, timeouts, and complete gate dependencies. Dependency Review, where present,
blocks the same gate for pull requests and reports other events as not applicable.
Renovate groups CodeQL, other Actions and coupled reusable workflow references in one CI update.

Auto-merge verifies strict branch protection for CI gate, requests native GitHub
auto-merge, and exits. GitHub waits for CI, external checks and required reviews.
External check contexts retain their required status and are bound to their source GitHub App. Removing automerge
or converting a PR to draft disables an existing request. The metadata-only workflow
runs from the trusted base without executing PR code. BOT_PAT remains the merger token;
Metadata approval retains the existing author-specific token and trusted-event checks.

## Recovery dependency locks

Renovate updates the exact recovery runtime and TLS test requirement inputs,
then regenerates their Python 3.14 Linux locks using literal allowlisted uv
0.13.4 commands with shell execution disabled. `--no-build` forbids source
builds; hashes, Linux qualification and the separate test/runtime scopes stay
in effect. Review the source and generated lock together.

```sh
uv pip compile recovery/runtime/requirements.in --generate-hashes --python-version 3.14 --python-platform linux --no-build --output-file recovery/runtime/requirements.lock
uv pip compile --python-version 3.14 --python-platform linux --generate-hashes --no-emit-index-url --no-build tests/tls/recovery-test-requirements.in --output-file tests/tls/recovery-test-requirements.lock
```
