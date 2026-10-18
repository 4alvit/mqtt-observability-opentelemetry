# Dashboard screenshot

The README shows the actual **MQTT Broker Overview** panel layout from source
revision `e93d923`, rendered in Grafana OSS 11.2.0 on 2026-10-17. The
[flat screenshot](mqtt-overview.jpg) is 1600 × 1000. The
[perspective hero](mqtt-hero.png) keeps those pixels and adds a neutral
background, rounded corners and a soft shadow.

All readings are **synthetic demonstration data**, not measured broker traffic
or evidence of deployment health. For the capture, a separate copy of the
dashboard replaces the Prometheus queries with Grafana's built-in TestData CSV
source. Panel layout, titles and display options come from the real dashboard.
No production data source, credential or running broker was used.

## Repeat the capture

1. Use a separate local Grafana instance. The capture used
   [Grafana OSS 11.2.0](https://grafana.com/grafana/download/11.2.0?edition=oss&platform=mac)
   with a fresh data directory, listening only on loopback. Do not change an
   existing instance's authentication or data sources for this task.
2. Provision the built-in TestData source with UID `synthetic-demo`:

   ```yaml
   apiVersion: 1
   datasources:
     - name: Synthetic demo
       type: grafana-testdata-datasource
       uid: synthetic-demo
       access: proxy
   ```

3. Import [`demo-dashboard.json`](demo-dashboard.json) into that disposable
   instance. This copy contains only fixed fictional CSV series and is for
   documentation; deploy the real files in `grafana-dashboards/` instead.
4. Open the imported dashboard at 1600 × 1000 with the fixed interval
   `from=1792231200000&to=1792234800000`. Collapse the navigation menu and retain
   the dashboard title, including **synthetic demo**. The capture used the
   `America/Los_Angeles` browser timezone and Grafana's `kiosk=tv` view.
5. Wait for the graphs to finish rendering, then save a viewport screenshot
   without browser chrome as `docs/screenshots/mqtt-overview.jpg`. Stop the
   disposable instance afterwards.
6. Run `bash scripts/render-docs-hero.sh` with ImageMagick 7. The renderer checks
   source dimensions and preserves all interface content. Exact repeatability
   was verified with ImageMagick 7.1.2-31; resampling can differ across versions.

When the production dashboard changes, refresh this demonstration copy from
the new layout before recapturing. Keep its data fictional and label the source
revision and Grafana version here.
