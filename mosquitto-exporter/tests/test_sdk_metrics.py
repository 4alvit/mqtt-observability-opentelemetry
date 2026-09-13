"""Check initialization and exported samples against the installed OTel SDK."""

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from mosquitto_exporter import SYSMetricsCollector, app
from mosquitto_exporter.config import Config, OTelConfig, PrometheusConfig


def test_collector_initializes_and_exports_named_gauge_values(monkeypatch):
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(app.metrics, "set_meter_provider", lambda _: None)
    monkeypatch.setattr(app.metrics, "get_meter", provider.get_meter)
    config = Config(otel=OTelConfig(endpoint=""), prometheus=PrometheusConfig(enabled=False))

    try:
        collector = SYSMetricsCollector(config)
        collector._parse_and_store("$SYS/broker/version", "2.0.18")
        collector._parse_and_store("$SYS/broker/clients/connected", "5")
        collector._parse_and_store("$SYS/broker/clients/connected", "3")
        collector._parse_and_store("$SYS/broker/load/messages/received/1min", "12.5")

        data = reader.get_metrics_data()
        samples = {
            metric.name: [point.value for point in metric.data.data_points]
            for resource in data.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
        }
        assert samples["mosquitto_clients_connected"] == [3]
        assert samples["mosquitto_messages_received_1min"] == [12.5]
        assert "mosquitto_gauges" not in samples
        assert "mosquitto_version" not in samples
        assert collector.metrics_data["mosquitto_version"] == "2.0.18"
    finally:
        provider.shutdown()
