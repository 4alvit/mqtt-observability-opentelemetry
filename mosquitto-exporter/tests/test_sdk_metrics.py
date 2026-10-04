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


def test_absolute_totals_repeat_reset_uptime_and_disconnected_gauge(monkeypatch):
    """Repeated retained totals must not inflate counters; broker restarts reset them."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(app.metrics, "set_meter_provider", lambda _: None)
    monkeypatch.setattr(app.metrics, "get_meter", provider.get_meter)
    config = Config(otel=OTelConfig(endpoint=""), prometheus=PrometheusConfig(enabled=False))
    collector = SYSMetricsCollector(config)

    def sample(name):
        data = reader.get_metrics_data()
        if data is None:
            return []
        return [
            p.value
            for r in data.resource_metrics
            for s in r.scope_metrics
            for m in s.metrics
            if m.name == name
            for p in m.data.data_points
        ]

    try:
        for value in (100, 100, 110, 4):
            collector._parse_and_store("$SYS/broker/messages/received", str(value))
            assert sample("mosquitto_messages_received_total") == [value]
        collector._parse_and_store("$SYS/broker/uptime", "42 seconds")
        assert sample("mosquitto_uptime_seconds") == [42]
        collector._parse_and_store("$SYS/broker/clients/disconnected", "5")
        collector._parse_and_store("$SYS/broker/clients/disconnected", "2")
        assert sample("mosquitto_clients_disconnected") == [2]
        data = reader.get_metrics_data()
        metric = next(
            m
            for r in data.resource_metrics
            for s in r.scope_metrics
            for m in s.metrics
            if m.name == "mosquitto_clients_disconnected"
        )
        assert type(metric.data).__name__ == "Gauge"
        collector._parse_and_store("$SYS/broker/load/bytes/received/1min", "nan")
        assert "mosquitto_bytes_received_1min" not in collector.metrics_data
        monkeypatch.setattr(app.time, "monotonic", lambda: collector.last_update + 121)
        assert sample("mosquitto_messages_received_total") == []
    finally:
        provider.shutdown()
