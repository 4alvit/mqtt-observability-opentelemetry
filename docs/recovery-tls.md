# Kubernetes recovery TLS boundary

The recovery client's service-account bearer token is sent only after the
connection validates the configured service-account CA, API hostname, TLS 1.2
or newer, and the actual verified certificate chain including its trust anchor.
RSA moduli must contain at least 2048 bits; EC keys at least 224 bits; DSA keys
require at least 2048-bit p and 224-bit q. Ed25519 and Ed448 are accepted.
Unsupported keys or runtimes without a verified-chain API fail before HTTP.
This is a per-client policy: other Python clients and global trust stores are
not modified. HTTP/1.1 ALPN remains explicit for this urllib transport.

The client retains its existing service-account token/CA paths, API endpoint
environment variables and 30-second request timeout. It rejects all HTTP
redirects rather than forwarding a Kubernetes bearer token to another URL.
Standard HTTP proxies can tunnel the verified origin connection. An actually
selected HTTPS-scheme proxy is rejected before connecting because this urllib
transport does not implement TLS to that proxy; `NO_PROXY` still permits direct
API access. Use an approved HTTP tunnel proxy or bypass the proxy for the API.

The implementation requires the cryptography dependency and a supported
CPython verified-chain API. The accompanying loopback tests exercise CPython
3.11 and 3.14; the intended deployment runtime is the hash-locked Linux
CPython 3.14 recovery image. No Kubernetes cluster, live service-account token
or system trust change is used by these tests.

## Deployment prerequisite

The CronJob and ConfigMap must be updated atomically. The ConfigMap includes
`tls_policy.py`; the image must provide `cryptography`. The plain Python image
used before this change cannot run the policy.

The manifest pins the **verified v0.2.4 release artifact** digest
`sha256:278fe9cf32be53f1b1503d9ea2b9c0c59c07804af94ee1164ae5feb4cfac5aac`
for `ghcr.io/4alvit/mqtt-observability-opentelemetry/recovery-runtime`.
The source release is `0c0ef89450aedd35fbf6f7d538ec839a32582a5b` and its
`recovery-runtime-container.oci.tar` contains both Linux amd64 and arm64.
At preparation on 2026-10-10, authenticated registry reads of tag `0.2.4` and the pinned digest
returned `MANIFEST_UNKNOWN`. **Do not merge or deploy until the normal approved
release workflow publishes that exact verified asset and the digest is readable
for both platforms.** Do not substitute a rebuild or an invented digest.

Image selection preserves the existing explicit user/group, read-only mounts,
capability and storage policies. The CronJob remains suspended by default. This
source change does not apply Kubernetes resources, start a backup or alter live
storage. Verify the rendered ConfigMap and image together before the separately
approved rollout.

CI runs the same actual-client tests on CPython 3.11/3.14 and inside the exact Linux recovery runtime for amd64/arm64. The runtime tests have no external
network access and use synthetic tokens and temporary CA keys. This document
covers only the backup client's outbound Kubernetes HTTPS path.
