"""Check initialization and exported samples against the installed OTel SDK."""

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from mosquitto_exporter import SYSMetricsCollector, app
from mosquitto_exporter.config import Config, MetricsConfig, OTelConfig, PrometheusConfig


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


def test_flashmq_numeric_values_labels_reset_and_individual_staleness(monkeypatch):
    """Periodic FlashMQ samples remain separate, bounded and absent when missing."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(app.metrics, "set_meter_provider", lambda _: None)
    monkeypatch.setattr(app.metrics, "get_meter", provider.get_meter)
    now = [1000.0]
    monkeypatch.setattr(app.time, "monotonic", lambda: now[0])
    collector = SYSMetricsCollector(
        Config(
            metrics=MetricsConfig(broker_type="flashmq", flashmq_threads=2),
            otel=OTelConfig(endpoint=""),
            prometheus=PrometheusConfig(enabled=False),
        )
    )

    def samples():
        data = reader.get_metrics_data()
        return (
            {}
            if data is None
            else {
                m.name: m.data.data_points
                for r in data.resource_metrics
                for s in r.scope_metrics
                for m in s.metrics
            }
        )

    try:
        for value in (100, 100, 104, 3):
            collector._parse_and_store("$SYS/broker/load/messages/received/total", str(value))
            assert [p.value for p in samples()["flashmq_messages_received_total"]] == [value]
        for topic, value in {
            "$SYS/broker/clients/total": "25",
            "$SYS/broker/load/messages/sent/total": "542",
            "$SYS/broker/load/messages/received/persecond": "4.5",
            "$SYS/broker/load/messages/sent/persecond": "6.75",
            "$SYS/broker/subscriptions/count": "42",
            "$SYS/broker/retained messages/count": "6",
            "$SYS/broker/sessions/total": "28",
            "$SYS/broker/threads/0/drift/latest__ms": "0",
            "$SYS/broker/threads/1/drift/latest__ms": "17",
            "$SYS/broker/threads/0/drift/moving_avg__ms": "3",
        }.items():
            collector._parse_and_store(topic, value)
        data = samples()
        assert data["flashmq_clients_connected"][0].value == 25
        assert data["flashmq_messages_sent_total"][0].value == 542
        assert data["flashmq_messages_received_per_second"][0].value == 4.5
        assert data["flashmq_messages_sent_per_second"][0].value == 6.75
        assert data["flashmq_subscriptions_count"][0].value == 42
        assert data["flashmq_retained_messages_count"][0].value == 6
        assert data["flashmq_sessions_count"][0].value == 28
        assert [
            (p.attributes["thread"], p.value) for p in data["flashmq_thread_drift_milliseconds"]
        ] == [("0", 0), ("1", 17)]
        assert [
            (p.attributes["thread"], p.value)
            for p in data["flashmq_thread_drift_moving_average_milliseconds"]
        ] == [("0", 3)]
        assert all(name.startswith("flashmq_") for name in data)
        assert not any("uptime" in name or "bytes" in name or "version" in name for name in data)
        collector._parse_and_store("$SYS/broker/sessions/total", "12")
        assert samples()["flashmq_sessions_count"][0].value == 12
        collector._parse_and_store("$SYS/broker/load/messages/sent/persecond", "nan")
        assert samples()["flashmq_messages_sent_per_second"][0].value == 6.75
        now[0] += 121
        collector._parse_and_store("$SYS/broker/threads/0/drift/latest__ms", "2")
        data = samples()
        assert set(data) == {"flashmq_thread_drift_milliseconds"}
        assert [
            (p.attributes["thread"], p.value) for p in data["flashmq_thread_drift_milliseconds"]
        ] == [("0", 2)]
        now[0] += 121
        assert samples() == {}
    finally:
        provider.shutdown()


@pytest.mark.parametrize(
    "broker,topic,metric",
    [
        ("mosquitto", "$SYS/broker/messages/received", "mosquitto_messages_received_total"),
        ("flashmq", "$SYS/broker/load/messages/received/total", "flashmq_messages_received_total"),
    ],
)
def test_counter_reset_uses_new_stream_for_delta_reader(monkeypatch, broker, topic, metric):
    from opentelemetry.sdk.metrics import ObservableCounter
    from opentelemetry.sdk.metrics.export import AggregationTemporality

    reader = InMemoryMetricReader(
        preferred_temporality={ObservableCounter: AggregationTemporality.DELTA}
    )
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(app.metrics, "set_meter_provider", lambda _: None)
    monkeypatch.setattr(app.metrics, "get_meter", provider.get_meter)
    collector = SYSMetricsCollector(
        Config(
            metrics=MetricsConfig(broker_type=broker),
            otel=OTelConfig(endpoint=""),
            prometheus=PrometheusConfig(enabled=False),
        )
    )
    try:
        collected = []
        for value in (100, 110, 4, 9):
            collector._parse_and_store(topic, str(value))
            data = reader.get_metrics_data()
            point = next(
                p
                for r in data.resource_metrics
                for s in r.scope_metrics
                for m in s.metrics
                if m.name == metric
                for p in m.data.data_points
            )
            collected.append(point)
        assert [p.value for p in collected] == [100, 10, 4, 5]
        assert [p.attributes["counter_epoch"] for p in collected] == [0, 0, 1, 1]
        assert collected[2].start_time_unix_nano > collected[0].start_time_unix_nano
    finally:
        provider.shutdown()


def test_unchanged_mosquitto_counter_remains_while_heartbeat_arrives(monkeypatch):
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(app.metrics, "set_meter_provider", lambda _: None)
    monkeypatch.setattr(app.metrics, "get_meter", provider.get_meter)
    clock = [1000.0]
    monkeypatch.setattr(app.time, "monotonic", lambda: clock[0])
    collector = SYSMetricsCollector(
        Config(otel=OTelConfig(endpoint=""), prometheus=PrometheusConfig(enabled=False))
    )
    try:
        collector._parse_and_store("$SYS/broker/messages/received", "100")
        clock[0] += 121
        collector._parse_and_store("$SYS/broker/uptime", "3600 seconds")
        data = reader.get_metrics_data()
        points = [
            p
            for r in data.resource_metrics
            for s in r.scope_metrics
            for m in s.metrics
            if m.name == "mosquitto_messages_received_total"
            for p in m.data.data_points
        ]
        assert [p.value for p in points] == [100]
    finally:
        provider.shutdown()
