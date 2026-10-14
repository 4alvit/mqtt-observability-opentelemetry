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

The exporter creates a verified Paho TLS context when `tls_enabled` is true.
The context checks the broker's complete verified certificate chain before
Paho can send a CONNECT packet, including the username/password. Configured
client certificate chains are checked before the connection starts. HTTPS OTLP
export and Kubernetes recovery use equivalent exact key checks on their own
connections. See [the transport guide](docs/otlp-transport.md),
[`mqtt_tls.py`](mosquitto-exporter/src/mosquitto_exporter/mqtt_tls.py) and
[`recovery/tls_policy.py`](recovery/tls_policy.py).

Supported contexts require TLS 1.2 or later, certificate and hostname validation,
and OpenSSL security level 2 or higher. Exact checks reject RSA keys below
2048 bits, elliptic-curve keys below 224 bits and DSA parameters below
2048/224 bits, including trust anchors. Ed25519 and Ed448 are supported.
OpenSSL security level 2 alone is insufficient: some builds classify a
2047-bit RSA key as equivalent to a 2048-bit key. The explicit verified-chain
check closes that boundary and fails closed when the runtime cannot expose
the verified chain. It does not replace normal certificate validation.

The TLS regression suite uses synthetic loopback servers and keys to test
TLS 1.2/1.3, accepted strong keys, rejected weak leaf/intermediate/root keys,
wrong hostnames and client-certificate chains. Run `bash scripts/ci.sh tls`
on supported CPython 3.11 and 3.14 environments. No deployed trust store or
credentials are needed. A successful test describes that tested runtime;
operators must preserve its validation settings and upgrade obsolete keys.

Plaintext local MQTT, HTTP/gRPC and interceptor listeners are not secured by
these contexts. Isolate them on trusted networks or deploy an independently
verified secure boundary. Operator-managed ingress, SSH and infrastructure
services require their own key and identity policies.

Release helpers also invoke `gh`, which has a separate TLS implementation.
Follow [the release helper profile](docs/RELEASE_TLS_PROFILE.md) for the tested
GitHub CLI/Go versions and the command-local option that completely disables
smaller certificate keys. That option does not change system-wide settings.

References: [OpenSSL security levels](https://docs.openssl.org/3.5/man3/SSL_CTX_set_security_level/)
and [Python SSL contexts](https://docs.python.org/3.12/library/ssl.html#ssl.SSLContext).

## Reporter credit

With the reporter's consent, fixed vulnerability advisories and release notes credit the person or organization that reported the issue. Respect requests for anonymity. Record affected versions, mitigation and the fixed revision without disclosing private reproduction data or credentials.
