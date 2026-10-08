# Prepared recovery runtime

This image adds a hash-locked cryptography runtime to the exact Python base
already used by the recovery CronJob. It is built and probed on Linux amd64 and
arm64. It contains no Kubernetes credentials, deployment manifests or backup
payloads. Its default user and group are unprivileged numeric ID 65532. The
current CronJob explicitly overrides both IDs to 0 for its existing read-only
source-volume access with `DAC_READ_SEARCH`; that deployment setting and
`recovery/backup.py` remain unchanged. Selecting an image does not silently
change the CronJob's volume-access contract.

The normal release asset builder produces `recovery-runtime-container.oci.tar`
with its version label, checksum and source-policy binding. The existing
`scripts/publish_verified.py` container path can copy the verified release asset
to `ghcr.io/4alvit/mqtt-observability-opentelemetry/recovery-runtime`; it must not
be replaced with an independent rebuild. Registry authentication and release
approval remain the existing operator workflow.

After publication, resolve the real registry digest with the existing verified
image workflow. A separate reviewed change can then pin that immutable digest
in the CronJob and enable the same-connection Kubernetes TLS policy. Until then,
this image preparation does not fix or claim coverage of Kubernetes API TLS.
No placeholder digest or cluster apply is part of this change.

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
