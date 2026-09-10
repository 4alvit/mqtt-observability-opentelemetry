#!/usr/bin/env bash
# Apply lean observability stack (prometheus + tempo + grafana + exporters).
# Usage: export KUBECONFIG=~/.kube/h7.yaml; ./deploy/k3s/deploy.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
KUBECTL="${KUBECTL:-kubectl}"

echo "Applying observability kustomize..."
"${KUBECTL}" apply -k "${DIR}"
"${KUBECTL}" -n observability rollout status deployment/prometheus --timeout=300s || true
"${KUBECTL}" -n observability rollout status deployment/tempo --timeout=300s || true
"${KUBECTL}" -n observability rollout status deployment/grafana --timeout=300s || true
"${KUBECTL}" -n observability rollout status deployment/kube-state-metrics --timeout=300s || true
"${KUBECTL}" -n observability rollout status daemonset/node-exporter --timeout=300s || true
"${KUBECTL}" -n observability get pods -o wide
