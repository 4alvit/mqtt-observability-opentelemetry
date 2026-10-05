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
[`924197b2bfadd18d1cfe2d7d760ab4ad2e097f3d`](https://github.com/4alvit/mqtt-observability-opentelemetry/commit/924197b2bfadd18d1cfe2d7d760ab4ad2e097f3d).
Their manifest pins are respectively `sha256:591f237c711f1673f66f4cbe7fcd2a78e8e96b80ec8a163927b8304e77ef4b58`
and `sha256:438a08943e741a50632dbf7948f2b6b3f982972c56bb9ed52dd73e77e6c40291`.
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

Both readers expose `GET /ready` on their existing metrics listener (9464 for
the observer, 9494 for the exporter). Readiness reads a small, locked local
snapshot without collecting Prometheus/OTel metrics, contacting the broker or
starting another Python process. It returns 503 before a valid receipt, after a
disconnect or unsuccessful connection, and when receipt age reaches 45 seconds;
a successful reconnect requires a new sample. Receipt age uses a monotonic
clock and invalid or future ages fail closed. `/metrics`, its content negotiation,
compression and query behavior remain unchanged; no listener or port is added.

The FlashMQ exporter requires a finite, nonnegative client-count sample from the
current connection, received within 45 seconds. Its individual exported
statistics also expire after 45 seconds without an update. The generic Mosquitto
profile allows other fresh accepted broker statistics to refresh a client count
already observed in this connection, because unchanged Mosquitto SYS values may
not be republished. The observer requires a receipt on a configured topic within
45 seconds; application payloads do not need to be numeric. Neither endpoint
publishes MQTT messages.

Each Python reader requests 250m CPU and retains its 500m CPU limit. In the loaded
MP snapshot on 2026-10-05 UTC, their former 50m reservations had cgroup weight 2.
Over ten seconds, observer/exporter cgroups consumed about 450m/292m CPU while
their main processes consumed only 76m/31m; separate readiness Python processes
and CPU throttling were observed. An SDK-importing probe exceeded a ten-second
diagnostic alarm. Even a `python -S` standard-library candidate took 10.4 seconds
for imports and validation (26.1 seconds including kubectl/CRI transport), so an
exec probe could not meet the unchanged five-second limit. The in-process HTTP
check removes that repeated startup cost. These measurements do not by
themselves attribute every cold-start delay to CPU contention.

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
