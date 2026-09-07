# Lean observability on k3s

Namespace `observability`: Prometheus + Tempo + Grafana.
No personal domains / Ingress hostnames — ClusterIP + port-forward only.

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
```

## Port-forward

```bash
kubectl -n observability port-forward svc/grafana 3000:3000
kubectl -n observability port-forward svc/prometheus 9090:9090
kubectl -n observability port-forward svc/tempo 3200:3200
```

## PVCs

`prometheus-data`, `tempo-data`, `grafana-data` — 5Gi each, `local-path`.
