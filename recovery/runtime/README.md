# Prepared recovery runtime

This image adds a hash-locked cryptography runtime to the exact Python base
already used by the recovery CronJob. It is built and probed on Linux amd64 and
arm64. It contains no Kubernetes credentials, deployment manifests or backup
payloads. Its default user and group are unprivileged numeric ID 65532. The
current CronJob explicitly overrides both IDs to 0 for its existing read-only
source-volume access with `DAC_READ_SEARCH`; that deployment setting and
the source-volume access contract remain unchanged. Selecting an image does
not silently change the CronJob's volume-access contract.

The normal release asset builder produces `recovery-runtime-container.oci.tar`
with its version label, checksum and source-policy binding. The existing
`scripts/publish_verified.py` container path can copy the verified release asset
to `ghcr.io/4alvit/mqtt-observability-opentelemetry/recovery-runtime`; it must not
be replaced with an independent rebuild. Registry authentication and release
approval remain the existing operator workflow.

The Kubernetes recovery client now uses this runtime's cryptography dependency.
The CronJob pins the digest of the verified v0.2.4 release asset; that exact asset
must be published through the existing release workflow before merging or
applying the deployment change. See [the TLS deployment prerequisite](../../docs/recovery-tls.md).
The source and runtime must be updated together: the plain Python image cannot
run the TLS policy. The CronJob remains suspended by default.

Regenerate `requirements.lock` with the repository's uv version:

```sh
uv pip compile --python-version 3.14 --python-platform linux \
  --generate-hashes --no-emit-index-url \
  recovery/runtime/requirements.in -o recovery/runtime/requirements.lock
```

The Dockerfile requires wheels and hashes, runs `pip check`, and does not upgrade
unrelated tooling. `tests/tls/recovery_runtime_probe.py` checks the real image's
Python/cryptography versions, default UID/GID, verified-chain API and exact
RSA/EC key decoding. CI runs those probes with no network and a read-only
container filesystem.

The recovery CI job uses this exact runtime to run the actual Kubernetes client
against synthetic loopback PKI on amd64 and arm64. Only the hash-locked pytest
harness is installed into temporary `/tmp`, from predownloaded wheels; the image
is never modified. Tests run without external networking, with a read-only
filesystem and disposable `/tmp`; no real token, cluster or backup volume is mounted.
