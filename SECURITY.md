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


## Cryptographic implementation and platform policy

Use current supported Python and TLS/SSH libraries. HTTPS requests retain the
library's certificate verification; do not disable verification to work around
an endpoint error. The audited Python 3.12.14/OpenSSL 3.5.8 default TLS context
requires TLS 1.2 or later, security level 2, at least 128-bit symmetric encryption
and ephemeral key exchange. Retain those requirements on the deployed runtime.
The project delegates cryptographic primitives to FLOSS libraries; it does not
implement a cipher or a random-number generator for keys/nonces. Telemetry
sampling and retry jitter, where present, are not cryptographic operations.

Use authenticated encrypted transport whenever credentials or private data leave
a trusted isolated network. A local plaintext MQTT/HTTP option is not encrypted
by these TLS defaults. Operators must separately verify remote certificates or
SSH host-key fingerprints and replace obsolete endpoint keys. The source and
release downloads are served through GitHub HTTPS.

## Verifying key-length policy in the deployed environment

The exporter delegates MQTT TLS to Paho when `tls_enabled` is true. `recovery/backup.py` uses `ssl.create_default_context` and the service-account CA for Kubernetes HTTPS. The MQTT interceptor and other plaintext local listeners require a separately secured deployment boundary.

The verified Python profile is CPython 3.12.14 with OpenSSL 3.5.8 and
SSL security level 2. At this level OpenSSL rejects RSA/DH keys shorter than
2048 bits and elliptic-curve keys shorter than 224 bits. It applies the check
to certificate-chain keys as well as negotiated parameters. Use current
supported runtime builds which preserve this policy; do not lower the security
level or turn off certificate/hostname verification to accept an old endpoint.
Check the interpreter which actually runs the application:

```sh
python - <<'PYTHON'
import ssl
import sys
context = ssl.create_default_context()
print(sys.version)
print(ssl.OPENSSL_VERSION)
print(context.security_level, context.minimum_version.name)
if context.security_level < 2 or context.minimum_version < ssl.TLSVersion.TLSv1_2:
    raise SystemExit("Unsupported TLS policy: upgrade the runtime; do not weaken verification")
PYTHON
```

On 2026-10-08, isolated local handshakes using the project's installed Python
client libraries rejected a trusted RSA-1024 server certificate and accepted
RSA-2048 and ECDSA P-256 certificates. This is evidence for that tested runtime,
not a claim about every operating-system TLS build or custom client configuration.

Release helpers also invoke `gh`, which has a separate TLS implementation.
Follow [the release helper profile](docs/RELEASE_TLS_PROFILE.md) for the tested
GitHub CLI/Go versions and the command-local option that completely disables
smaller certificate keys. That option does not change system-wide settings.

References: [OpenSSL security levels](https://docs.openssl.org/3.5/man3/SSL_CTX_set_security_level/)
and [Python SSL contexts](https://docs.python.org/3.12/library/ssl.html#ssl.SSLContext).
