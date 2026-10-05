# MQTT telemetry on MP

This separate overlay adds three singleton workloads to the existing
`observability` namespace. It does not redeploy the existing monitoring stores,
change the MQTT broker, or redirect application traffic.

- `mosquitto-exporter` uses the explicit FlashMQ profile for Cerbo 1.23.2,
  including counters, connected clients and its one worker's scheduler drift.
  Exact `$SYS` subscriptions are read-only; no device payloads are consumed.
- `mqtt-interceptor` passively observes only
  `$SYS/broker/load/messages/received/total` at the broker's ten-second interval.
  One small consumer span per heartbeat proves the MQTT-to-OTLP-to-Tempo path.
  This is broker heartbeat tracing, not full application or command tracing.
- `mqtt-otel-collector` sends those spans to existing Tempo and exposes the
  exporter metrics for existing Prometheus pod discovery. It has bounded memory,
  batching and queues, no debug payload exporter, and no persistent storage.

The observer and exporter images were built from source
[`0e22c6e357a51cb58b394712f47bf15cd87c7c6b`](https://github.com/4alvit/mqtt-observability-opentelemetry/commit/0e22c6e357a51cb58b394712f47bf15cd87c7c6b).
Their manifest pins are respectively `sha256:53479a0aad7aa8e7d7b08945f57392db021a948587b98fa42e9c1747e70cb3cf`
and `sha256:ef96d818c7a3099aa9bb99ca7108eb783502e01c98ecafd0dad1aa1ea1b58136`.
All images are immutable digests. Each workload is fixed to one replica on MP,
uses a read-only root, no privileges or service-account token, and has bounded
CPU/memory. NetworkPolicy permits only DNS, the existing Cerbo MQTT endpoint,
internal OTLP/Tempo traffic and Prometheus scrapes. There is no public ingress.
MQTT/OTLP use the existing trusted private network; no Internet listener is added.

Service links are disabled on all three pods: Kubernetes otherwise injects a
`PROMETHEUS_PORT=tcp://...` value from the existing Prometheus Service, which the
exporter's integer port setting rejects. The exporter also explicitly selects
port 9494. Both readers retain their unique `OTEL_SERVICE_NAME`; the optional
`OTEL_RESOURCE_ATTRIBUTES` override is omitted because its former JSON value
conflicts with the standard SDK detector's comma-separated key/value format.

## Apply and acceptance

Use the reviewed committed overlay only after all required checks pass:

```bash
kubectl --context k3s-heaven diff -k deploy/mqtt-k3s
kubectl --context k3s-heaven apply --server-side --field-manager=mqtt-observability-deploy -k deploy/mqtt-k3s
kubectl --context k3s-heaven -n observability rollout status deployment/mqtt-otel-collector
kubectl --context k3s-heaven -n observability rollout status deployment/mosquitto-exporter
kubectl --context k3s-heaven -n observability rollout status deployment/mqtt-interceptor
```

The exporter readiness probe requires a fresh FlashMQ client-count sample;
individual FlashMQ statistics expire after 45 seconds without an update. The
observer readiness probe requires a heartbeat received within 45 seconds. Both
probes parse exact Prometheus sample names with optional labels, reject missing
or ambiguous series and non-finite values, and fail closed for invalid ages.
All startup, readiness and liveness requests allow five seconds for scheduling.
Startup allows 36 attempts at five-second intervals (180 seconds): the initial
MP observer needed about 142 seconds to expose metrics during a loaded-node
cold start, exceeding the former 120-second budget. Readiness and liveness
thresholds, probe timeouts and the 45-second receipt freshness limit are unchanged.
The collector expires cached Prometheus samples after 60 seconds without a new
OTLP point; this is separate from the exporter's 45-second receipt limit. A point
exported just before that limit can therefore remain visible in the collector
for up to a further 60 seconds.
HTTP metrics alone are insufficient acceptance: check increasing broker counters
in Prometheus, fresh drift samples and an actual `cerbo-mqtt-observer` span in
Tempo. Check existing Venus MCP reads and its restart count before and after.

The collector runs upstream 0.161.0 through the existing NAS registry because
MP's direct Docker Hub pulls timed out during TLS negotiation. The cached Linux
AMD64 image from upstream reference
`otel/opentelemetry-collector-contrib:0.161.0@sha256:fd328de2552466ad78385e1b1289c3f2402b1c45f265b252aab1955b42845ac1`
was tagged and pushed without rebuilding. The mirror pin is
`192.168.167.25:5050/mqtt-otel-collector@sha256:b5cf983651c32c3ca13f936deb51742015a54d121f388cac248923ddeb8cc9fc`.
Source and mirror have the same image ID
`sha256:0fd3483345a3fa17f3ffe760eea2413f640724c8204ea11a58c8e3927b2c0fe7`
and all three RootFS layer hashes match. The repository manifest digest changes
with this native mirror; the verified image contents and collector configuration
are preserved. No TLS verification or firewall settings were changed.

The newer 0.162.0 GitHub release did not have a retrievable stable container
manifest when this overlay was prepared.
`collector-runtime.yaml` is the standalone copy used for upstream collector
`validate`; tests require it to equal the ConfigMap payload.

## Rollback and limits

To stop just these new readers and the collector, scale these three deployments
to zero. Existing MQTT clients, Prometheus, Grafana and Tempo remain running:

```bash
kubectl --context k3s-heaven -n observability scale deployment/mqtt-interceptor deployment/mosquitto-exporter deployment/mqtt-otel-collector --replicas=0
```

The original absence of broker metrics cannot be repaired retroactively. A new
healthy snapshot does not establish the cause of historical Venus timeouts or
prove uninterrupted long-duration stability. Tempo's existing retention policy
applies to these small heartbeat spans.
