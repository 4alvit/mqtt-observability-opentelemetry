# Lean observability on k3s

Namespace `observability`: Prometheus + Tempo + Grafana + node-exporter + kube-state-metrics.

## Ingress (Traefik VIP 10.0.0.58)

| Host | Service |
|------|---------|
| https://grafana.k3s.2560801.xyz/ | Grafana |
| https://prometheus.k3s.2560801.xyz/ | Prometheus UI (LAN) |

TLS via cert-manager `Certificate` → secret `k3s-wildcard-tls` (ClusterIssuer `letsencrypt-cloudflare`).

## Exporters

- **node-exporter** DaemonSet on **all** nodes (including mp/syn) — host metrics. Service `node-exporter:9100` (headless).
- **kube-state-metrics** Deployment — affinity `NotIn` mp,syn (runs on h5|h7|h8). Service `:8080`.

Prometheus scrapes both via `kubernetes_sd` `role: endpoints`, plus existing `kubernetes-pods` annotation scrape.

## Secrets (names only)

| Secret | Keys |
|--------|------|
| `grafana-admin` | `admin-user`, `admin-password` |

```bash
kubectl -n observability create secret generic grafana-admin \
  --from-literal=admin-user=admin \
  --from-literal=admin-password="$GRAFANA_ADMIN_PASSWORD" \
  --dry-run=client -o yaml | kubectl apply -f -
```

GitHub Actions secrets: `KUBECONFIG`, `GRAFANA_ADMIN_PASSWORD`.

## Apply / redeploy

The deployment script uses server-side apply because the frozen dashboard
ConfigMap exceeds the client-side annotation limit. Field ownership conflicts
stop deployment and must be reviewed; the script does not force ownership.

```bash
export KUBECONFIG=~/.kube/h7.yaml
./deploy/k3s/deploy.sh
# or workflow_dispatch on .github/workflows/deploy-k3s.yml
# After config change: kubectl -n observability rollout restart deploy/prometheus
```

## Port-forward (fallback)

```bash
kubectl -n observability port-forward svc/grafana 3000:3000
kubectl -n observability port-forward svc/prometheus 9090:9090
kubectl -n observability port-forward svc/tempo 3200:3200
```

## PVCs

Active stores use `prometheus-data-mp`, `tempo-data-mp`, and `grafana-data-mp`: 5Gi
static local volumes on mp with Retain. The original h7 claims remain declared
for rollback. Follow [the migration and recovery runbook](../../recovery/RUNBOOK.md);
applying this base is not the live migration procedure.

## Grafana dashboards

The `grafana-host-dashboards` ConfigMap contains the exact four JSON files captured from the running installation on 2026-09-16:

- Node Exporter Full (1860)
- Kubernetes / Views / Pods (15760)
- Kubernetes Cluster (Prometheus) (6417)
- Kubernetes Cluster (7249)

Datasource: provisioned `Prometheus`. Files and UIDs remain stable across pod starts; provenance and SHA-256 are in `dashboards/provenance.json`.

## Runtime security

Grafana, Prometheus and Tempo retain their upstream image UIDs (472, 65534 and
10001 respectively). They use read-only root filesystems, dropped capabilities,
no privilege escalation and RuntimeDefault seccomp. Application data remains on
the existing mount paths; `/tmp` uses bounded emptyDir volumes. Grafana reads
frozen provisioned dashboards from a ConfigMap without network downloads.

Prometheus keeps pod/endpoint discovery permissions; its configuration has no
kubelet scrape, so node-proxy and node-metrics permissions are removed.
Kube-state-metrics explicitly enables its v2.13 default resource collectors except
Secrets and no longer has Secret list/watch permission. Secret metadata metrics
are therefore intentionally absent. Other configured collectors are preserved.

The node exporter still observes the host network, PID namespace and read-only
host filesystems, as required by the [pinned upstream deployment guidance](https://github.com/prometheus/node_exporter/blob/v1.8.2/README.md#docker).
It runs as UID/GID 65534, drops all capabilities and receives no service-account
token. On 2026-09-13 the operator explicitly approved KSV-0009, KSV-0010,
KSV-0024 and KSV-0121 for this workload's existing host-monitoring access.
`.trivyignore.yaml` scopes those four exceptions to this one literal file path;
`trivy.yaml` explicitly selects that policy for local and hosted scans.

The security job runs `scripts/host_monitoring_policy.py` and its negative tests
before Bandit and Trivy. It checks both named resources, all non-root/read-only
controls, capabilities, token policy, exact image, paths and mount propagation.
A semantic fingerprint locks the complete reviewed DaemonSet and Service,
including every additional field. New workloads, sidecars, host paths, altered
arguments or a broader ignore policy fail before Trivy can apply the exceptions.
Any future manifest change therefore needs its exception reviewed before updating
the fingerprint. Other files and scanner findings remain subject to the unchanged
HIGH/CRITICAL gate.

The accepted residual risk remains: this process shares the host network and PID
namespace and can read host metadata and world-readable files. The decision does
not authorize write access, extra capabilities or additional host mounts.

`python scripts/kubernetes_smoke.py` (with the locked MQTT-interceptor environment)
checks the actual image identities, read-only roots, writable data volumes,
frozen dashboard files and readiness in a disposable local Docker stack. Its
Kubernetes discovery endpoint is deliberately disconnected. It does not validate
live cluster RBAC, existing PVC permissions or node firewall policy, and does not
deploy these manifests.
