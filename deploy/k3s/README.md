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

`prometheus-data`, `tempo-data`, `grafana-data` — 5Gi each, `local-path`.

## Grafana dashboards

`fetch-dashboards` initContainer downloads from grafana.com into an emptyDir, then Grafana file-provisions them:

- Node Exporter Full (1860)
- Kubernetes / Views / Pods (15760)
- Kubernetes Cluster (Prometheus) (6417)
- Kubernetes Cluster (7249)

Datasource: provisioned `Prometheus`. Re-fetched on every pod start (survives PVC wipe).

## Runtime security

Grafana, Prometheus and Tempo retain their upstream image UIDs (472, 65534 and
10001 respectively). They use read-only root filesystems, dropped capabilities,
no privilege escalation and RuntimeDefault seccomp. Application data remains on
the existing PVC paths; `/tmp` uses bounded emptyDir volumes. Grafana dashboard
bootstrap uses the pinned Python image's standard library and the shared fsGroup,
so pod startup no longer installs packages as root.

Prometheus keeps pod/endpoint discovery permissions; its configuration has no
kubelet scrape, so node-proxy and node-metrics permissions are removed.
Kube-state-metrics explicitly enables its v2.13 default resource collectors except
Secrets and no longer has Secret list/watch permission. Secret metadata metrics
are therefore intentionally absent. Other configured collectors are preserved.

The node exporter still observes the host network, PID namespace and read-only
host filesystems, as required by the [pinned upstream deployment guidance](https://github.com/prometheus/node_exporter/blob/v1.8.2/README.md#docker).
It runs as UID/GID 65534, drops all capabilities and receives no service-account
token. Trivy still reports KSV-0009, KSV-0010, KSV-0024 and KSV-0121 for that
workload. These four host-access findings require an explicit policy decision;
they are not waived by the runtime hardening.

`python scripts/kubernetes_smoke.py` (with the locked MQTT-interceptor environment)
checks the actual image identities, read-only roots, writable data volumes,
dashboard bootstrap and readiness in a disposable local Docker stack. Its
Kubernetes discovery endpoint is deliberately disconnected. It does not validate
live cluster RBAC, existing PVC permissions or node firewall policy, and does not
deploy these manifests.
