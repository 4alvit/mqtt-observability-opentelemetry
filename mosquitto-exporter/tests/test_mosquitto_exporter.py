"""Tests for Mosquitto Exporter."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mosquitto_exporter import SYS_METRICS, SYSMetric, SYSMetricsCollector
from mosquitto_exporter.config import (
    Config,
    MetricsConfig,
    MQTTConfig,
    OTelConfig,
    PrometheusConfig,
    get_config,
    load_config,
)


def create_mock_collector(config: Config) -> SYSMetricsCollector:
    """Create a collector with mocked OTel setup."""
    with patch.object(SYSMetricsCollector, "_setup_otel"):
        collector = SYSMetricsCollector(config)
    # Manually set up the attributes that _setup_otel would create
    collector.meter = MagicMock()
    collector.otel_metrics = {}
    return collector


class TestSYSMetrics:
    """Tests for SYS metric definitions."""

    def test_sys_metrics_defined(self):
        """Test that SYS_METRICS list is populated."""
        assert len(SYS_METRICS) > 0
        assert all(isinstance(m, SYSMetric) for m in SYS_METRICS)

    def test_sys_metrics_have_required_fields(self):
        """Test each metric has required fields."""
        for metric in SYS_METRICS:
            assert metric.topic
            assert metric.name
            assert metric.type in ("counter", "gauge")
            assert metric.description
            assert metric.value_type in (int, float, str)


class TestMQTTConfig:
    """Tests for MQTT configuration."""

    def test_default_values(self):
        """Test default configuration values."""
        config = MQTTConfig()
        assert config.upstream_host == "mosquitto"
        assert config.upstream_port == 1883
        assert config.version == 5
        assert config.client_id == "mosquitto-exporter"
        assert config.keepalive == 60


class TestOTelConfig:
    """Tests for OTEL configuration."""

    def test_export_interval_ms_property(self):
        """Test export_interval_ms property."""
        config = OTelConfig(export_interval=30)
        assert config.export_interval_ms == 30000

    def test_default_endpoint(self):
        """Test default OTEL endpoint."""
        config = OTelConfig()
        assert config.endpoint == "http://otelcol:4317"


class TestPrometheusConfig:
    """Tests for Prometheus configuration."""

    def test_default_values(self):
        """Test default Prometheus configuration."""
        config = PrometheusConfig()
        assert config.enabled is True
        assert config.port == 9494
        assert config.path == "/metrics"


class TestMetricsConfig:
    """Tests for Metrics configuration."""

    def test_default_sys_topics(self):
        """Test default SYS topics list."""
        config = MetricsConfig()
        assert len(config.sys_topics) > 0
        assert "$SYS/broker/uptime" in config.sys_topics
        assert "$SYS/broker/clients/connected" in config.sys_topics


class TestConfigLoading:
    """Tests for configuration loading."""

    def test_load_config_defaults(self):
        """Test loading configuration with defaults."""
        config = load_config()
        assert isinstance(config, Config)
        assert config.mqtt.upstream_host == "mosquitto"
        assert config.prometheus.enabled is True

    def test_get_config_singleton(self):
        """Test get_config returns singleton."""
        config1 = get_config()
        config2 = get_config()
        assert config1 is config2


class TestSYSMetricsCollector:
    """Tests for SYSMetricsCollector class."""

    def test_collector_creation(self):
        """Test creating collector with config."""
        config = Config()
        collector = create_mock_collector(config)

        assert collector.config == config
        assert collector.client is None
        assert collector.metrics_data == {}
        assert collector.last_update == 0
        assert collector.meter is not None
        assert collector.otel_metrics == {}

    def test_parse_and_store_valid_int(self):
        """Test parsing and storing integer values."""
        config = Config()
        collector = create_mock_collector(config)

        collector._parse_and_store("$SYS/broker/uptime", "3600")
        assert collector.metrics_data["mosquitto_uptime_seconds"] == 3600

    def test_parse_and_store_valid_float(self):
        """Test parsing and storing float values."""
        config = Config()
        collector = create_mock_collector(config)

        collector._parse_and_store("$SYS/broker/load/messages/received/1min", "12.5")
        assert collector.metrics_data["mosquitto_messages_received_1min"] == 12.5

    def test_parse_and_store_valid_string(self):
        """Test parsing and storing string values."""
        config = Config()
        collector = create_mock_collector(config)

        collector._parse_and_store("$SYS/broker/version", "2.0.18")
        assert collector.metrics_data["mosquitto_version"] == "2.0.18"

    def test_parse_and_store_invalid_value(self):
        """Test parsing handles invalid values gracefully."""
        config = Config()
        collector = create_mock_collector(config)

        # Should not raise, just log warning
        collector._parse_and_store("$SYS/broker/uptime", "not-an-int")
        # Metric should not be stored
        assert "mosquitto_uptime_seconds" not in collector.metrics_data

    def test_parse_and_store_unknown_topic(self):
        """Test parsing ignores unknown topics."""
        config = Config()
        collector = create_mock_collector(config)

        collector._parse_and_store("$SYS/unknown/topic", "123")
        # Should not crash, just ignore

    def test_record_metric_counter(self):
        """Test recording counter metric."""
        config = Config()
        collector = create_mock_collector(config)

        # Create a mock counter
        mock_counter = MagicMock()
        collector.otel_metrics["test_counter"] = mock_counter

        collector._record_metric("test_counter", 5, "counter")
        mock_counter.add.assert_called_once_with(5)

    def test_record_metric_gauge(self):
        """Test recording gauge metric (no-op for gauges)."""
        config = Config()
        collector = create_mock_collector(config)

        # Create a mock gauge
        mock_gauge = MagicMock()
        collector.otel_metrics["test_gauge"] = mock_gauge

        collector._record_metric("test_gauge", 42, "gauge")
        # Gauges are handled via observable callback, no add call


class TestCollectorCallbacks:
    """Tests for collector observable callbacks."""

    def test_observable_callback_returns_observations(self):
        """Test observable callback returns proper observations."""
        config = Config()
        collector = create_mock_collector(config)

        # Set some test data
        collector.metrics_data["mosquitto_uptime_seconds"] = 3600
        collector.metrics_data["mosquitto_clients_connected"] = 5

        # Create a mock options object
        options = MagicMock()

        observations = collector._observable_callback(options)

        # Should return observations for gauge metrics
        assert isinstance(observations, list)


@pytest.mark.asyncio
class TestSYSMetricsCollectorAsync:
    """Async tests for SYSMetricsCollector."""

    async def test_start_stop_lifecycle(self):
        """Test collector start and stop."""
        config = Config()
        collector = create_mock_collector(config)

        with patch("paho.mqtt.client.Client") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            with patch("prometheus_client.start_http_server"):
                # Start collector (will run briefly)
                task = asyncio.create_task(collector.start())
                await asyncio.sleep(0.1)
                collector.stop()
                await asyncio.wait([task], timeout=1.0)

            mock_client_class.assert_called_once()
            mock_client.connect_async.assert_called_once()
            mock_client.loop_start.assert_called_once()
            mock_client.loop_stop.assert_called_once()
            mock_client.disconnect.assert_called_once()

    async def test_on_connect_subscribes_to_topics(self):
        """Test on_connect subscribes to all SYS topics."""
        config = Config()
        collector = create_mock_collector(config)

        mock_client = MagicMock()
        mock_reason_code = MagicMock()
        mock_properties = MagicMock()

        collector._on_connect(mock_client, None, None, mock_reason_code, mock_properties)

        # Should subscribe to all SYS topics
        assert mock_client.subscribe.call_count == len(SYS_METRICS)
