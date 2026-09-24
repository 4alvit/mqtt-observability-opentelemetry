# Venus metrics in the Kubernetes Prometheus

`deploy/k3s/prometheus-config.yaml` includes the `venus-os-observability` and
`inverter-control` jobs on the Venus device's internal address, ports 9090 and
9102. Synology Grafana uses `https://prometheus.k3s.2560801.xyz`; the retired
Synology Docker-host endpoint `172.17.0.1:9090` must not be used.

For controller metrics, configure its device-local `metrics.env` with
`INVERTER_METRICS_HOST=192.168.160.150` and restart under its supervisor. The
application's default remains loopback. See the inverter-control repository's
`docs/prometheus-alerts.md` for remote-access scope and persistent settings.

Validate the candidate configuration with `promtool check config`, then apply
only the ConfigMap. Wait until `/etc/prometheus/prometheus.yml` in the running
pod contains the new jobs, then POST `/-/reload`; no pod restart is required.
Do not apply the entire deployment just to reload scrape settings.

Verify from the running Prometheus:

- Both targets appear and are healthy in `/api/v1/targets`.
- `up{job=~"venus-os-observability|inverter-control"}` returns two values of 1.
- `sum(rate(victron_dbus_signals_received_total[5m]))` is positive once there
  are at least two samples.
- Synology Grafana evaluates all four Venus rules without query errors.

The notification policy and its safe deployment script live in
`4alvit/terraform-portainer-synology/deployments/inverter-monitoring/runtime/`.
