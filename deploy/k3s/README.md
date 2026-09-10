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
