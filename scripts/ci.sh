#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "${1:-}" == security || "${1:-}" == bandit ]]; then
  uvx --from bandit==1.8.6 bandit -r . -lll -x .git,.venv,.venv-ci,tests,scripts/release.py,scripts/release_control.py
  if [[ "${1:-}" == security ]]; then
    command -v trivy >/dev/null || { echo 'Trivy is required for the complete local security gate.' >&2; exit 1; }
    trivy fs --scanners vuln,secret,misconfig --severity HIGH,CRITICAL --exit-code 1 --skip-dirs .git,.venv,.venv-ci,release-dist,dist,build .
  fi
  exit 0
fi
mode="${1:-all}"
[[ "$mode" =~ ^(all|lint|test|integration)$ ]] || { echo 'Usage: ci.sh [lint|test|integration|security] [component]' >&2; exit 2; }
components=(mqtt-interceptor mosquitto-exporter)
if [[ -n "${2:-}" ]]; then
  [[ "$2" == mqtt-interceptor || "$2" == mosquitto-exporter ]] || exit 2
  components=("$2")
fi
if [[ "$mode" != integration ]]; then
  for component in "${components[@]}"; do
    uv sync --project "$component" --locked --extra dev
    if [[ "$mode" != test ]]; then
      (cd "$component"; uv run --locked ruff check src/; uv run --locked ruff format --check src/; uv run --locked mypy src/)
    fi
    if [[ "$mode" != lint ]]; then
      (cd "$component"; PYTHONPATH="$PWD/src" uv run --locked pytest tests/ --cov=src --cov-report=xml)
    fi
  done
fi
if [[ "$mode" == all || "$mode" == integration ]]; then
  uv run --project mqtt-interceptor --locked python "$PWD/scripts/compose_smoke.py"
fi
