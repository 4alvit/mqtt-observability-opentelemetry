#!/usr/bin/env bash
# Apply lean observability stack (prometheus + tempo + grafana + exporters).
# Usage: SOURCE_SHA=<reviewed full SHA> ./deploy/k3s/deploy.sh --apply
# Without --apply, only render the reviewed configuration locally.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
KUBECTL="${KUBECTL:-kubectl}"

REPO_ROOT="$(git -C "$DIR" rev-parse --show-toplevel)"
ACTUAL_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
[[ "${SOURCE_SHA:-}" =~ ^[0-9a-f]{40}$ && "$SOURCE_SHA" == "$ACTUAL_SHA" ]] || {
  echo "Set SOURCE_SHA to the full reviewed configuration commit" >&2
  exit 1
}
if [[ -n "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)" ]]; then
  echo "Deployment requires committed configuration" >&2
  exit 1
fi
if [[ "${1:---render}" == --render ]]; then
  exec "$KUBECTL" kustomize "$DIR"
fi
[[ "$1" == --apply ]] || { echo "Use --render or --apply" >&2; exit 1; }
RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED"' EXIT
"$KUBECTL" kustomize "$DIR" > "$RENDERED"
"$KUBECTL" apply -f "$RENDERED"
"${KUBECTL}" -n observability rollout status deployment/prometheus --timeout=300s
"${KUBECTL}" -n observability rollout status deployment/tempo --timeout=300s
"${KUBECTL}" -n observability rollout status deployment/grafana --timeout=300s
"${KUBECTL}" -n observability rollout status deployment/kube-state-metrics --timeout=300s
"${KUBECTL}" -n observability rollout status daemonset/node-exporter --timeout=300s
"${KUBECTL}" -n observability get pods -o wide
