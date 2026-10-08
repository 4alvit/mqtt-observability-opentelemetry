# OTLP transport and certificate keys

The interceptor exports traces and the Mosquitto exporter exports metrics.
Both retain the local demo's plaintext gRPC default. For TLS collectors, select
the HTTP/protobuf exporter explicitly; TLS gRPC now fails at startup because
the gRPC Python API does not expose the verified chain to this policy.

```sh
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_ENDPOINT=https://collector.example:4318
export OTEL_EXPORTER_OTLP_CERTIFICATE=/run/secrets/collector-ca.pem
export OTEL_EXPORTER_OTLP_HEADERS=authorization=Bearer%20TOKEN
```

Configure the collector's actual HTTPS listener. Changing the protocol does
not rewrite ports, enable collector TLS, or deploy anything. Keep credentials
outside source control and supply them through your deployment's secret store.

## Existing configuration and SDK options

`OTEL_ENDPOINT` remains the component's endpoint setting and `OTEL_TIMEOUT`
remains its timeout in seconds. For HTTP/protobuf, the base endpoint gets
`/v1/traces` or `/v1/metrics` appended. Standard signal-specific
`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` and `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`
override it and are used verbatim. The standard global
`OTEL_EXPORTER_OTLP_ENDPOINT` does not override the existing component setting.

Signal-specific `*_PROTOCOL` overrides the common protocol. Blank or whitespace
protocol values fall back to the common setting, then `grpc`. Unknown protocols
fail. `OTEL_INSECURE` retains its existing gRPC meaning; it cannot disable
verification for HTTP/protobuf. Plaintext HTTP and redirects are rejected.

The pinned OpenTelemetry SDK retains its standard common/signal-specific
headers, CA certificate, client certificate/key, compression, metric temporality
and aggregation options. The existing component timeout takes precedence over
SDK timeout environment variables. The previously unused component `headers`
field is not activated by this change: use the SDK `*_HEADERS` variables.
Metric collection interval, Prometheus reader, resource attributes, and MQTT
message handling are unchanged.

## Verified connection policy

Each owned HTTPS connection first performs normal CA and hostname verification,
then checks every certificate in that same verified chain, including the trust
anchor, before HTTP headers or bodies are sent. RSA modulus length must be at
least 2048 bits (2047 is rejected), EC at least 224 bits, and DSA at least
2048/224 bits. Ed25519 and Ed448 are accepted. Unknown keys and runtimes unable
to return the verified chain fail closed. TLS is at least 1.2; stricter protocol
and cipher selections are preserved.

HTTP and HTTPS CONNECT proxies retain Requests' routing and `NO_PROXY` behavior.
The HTTPS proxy's own chain is checked before CONNECT credentials, and the
origin's chain is checked inside the tunnel before OTLP data. SOCKS is unsupported.
Configured CA files/directories remain authoritative; fresh contexts prevent
trust roots from accumulating across reconnects. Client certificate/key files
are captured once, checked and loaded from private temporary files; malformed,
weak, mismatched or encrypted client keys fail without an interactive prompt.

The implementation is local to these exporter sessions and does not modify
Requests, urllib3, SSL, or unrelated clients globally. The supported dependency
contract is pinned in each component lock: OpenTelemetry 1.44.0, Requests 2.34.2,
urllib3 2.8.0 and cryptography 50.0.2. CI exercises CPython 3.11 and 3.14 on Linux;
other platform builds require compatible cryptography support and are not
claimed verified by these tests.

## Validation and remaining scope

Run `bash scripts/ci.sh tls` for real loopback trace/metric protobuf exports,
TLS 1.2/1.3, RSA/EC positive controls, weak leaf/intermediate/root cases including
RSA2047, unknown CA/wrong name, proxy, mTLS snapshot and configuration tests.
The low-strength context exists only in the test oracle to prove that each
synthetic chain is otherwise valid. Product rejection must occur before bytes.

This is evidence for the two OTLP HTTPS paths, not project-wide cryptographic
compliance. MQTT broker TLS, other integrations and deployment choices have
their own scope. The recovery CronJob still uses its existing image and
Kubernetes transport; the prepared [recovery runtime](../recovery/runtime/README.md)
must be published and pinned before that path can adopt the policy.

The transport starts from the MIT-licensed
[venus-os-observability implementation at c732d26](https://github.com/victron-venus/venus-os-observability/blob/c732d26a65f3259aab3442679375b41625310500/src/venus_observability/otlp.py).
Each self-contained component retains the upstream copyright and license text.
