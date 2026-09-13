"""Tests for Mosquitto Exporter."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from paho.mqtt.client import MQTTMessage
from pydantic import ValidationError

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


def test_mqtt_config_default_values():
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


def test_prometheus_config_default_values():
    """Test default Prometheus configuration."""
    config = PrometheusConfig()
    assert config.enabled is True
    assert config.port == 9494
    assert config.path == "/metrics"


def test_default_sys_topics():
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
        assert not collector.metrics_data
        assert collector.last_update == 0
        assert collector.meter is not None
        assert not collector.otel_metrics

    def test_parse_and_store_valid_int(self):
        """Test parsing and storing integer values."""
        config = Config()
        collector = create_mock_collector(config)

        # Verify the collector implementation directly in this focused unit test.
        collector._parse_and_store("$SYS/broker/uptime", "3600")  # pylint: disable=protected-access
        assert collector.metrics_data["mosquitto_uptime_seconds"] == 3600

    def test_parse_and_store_valid_float(self):
        """Test parsing and storing float values."""
        config = Config()
        collector = create_mock_collector(config)

        # Verify the collector implementation directly in this focused unit test.
        collector._parse_and_store("$SYS/broker/load/messages/received/1min", "12.5")  # pylint: disable=protected-access
        assert collector.metrics_data["mosquitto_messages_received_1min"] == 12.5

    def test_parse_and_store_valid_string(self):
        """Test parsing and storing string values."""
        config = Config()
        collector = create_mock_collector(config)

        # Verify the collector implementation directly in this focused unit test.
        collector._parse_and_store("$SYS/broker/version", "2.0.18")  # pylint: disable=protected-access
        assert collector.metrics_data["mosquitto_version"] == "2.0.18"

    def test_parse_and_store_invalid_value(self):
        """Test parsing handles invalid values gracefully."""
        config = Config()
        collector = create_mock_collector(config)

        # Should not raise, just log warning
        # Verify the collector implementation directly in this focused unit test.
        collector._parse_and_store("$SYS/broker/uptime", "not-an-int")  # pylint: disable=protected-access
        # Metric should not be stored
        assert "mosquitto_uptime_seconds" not in collector.metrics_data

    def test_parse_and_store_unknown_topic(self):
        """Test parsing ignores unknown topics."""
        config = Config()
        collector = create_mock_collector(config)

        # Verify the collector implementation directly in this focused unit test.
        collector._parse_and_store("$SYS/unknown/topic", "123")  # pylint: disable=protected-access
        # Should not crash, just ignore

    def test_record_metric_counter(self):
        """Test recording counter metric."""
        config = Config()
        collector = create_mock_collector(config)

        # Create a mock counter
        mock_counter = MagicMock()
        collector.otel_metrics["test_counter"] = mock_counter

        # Verify the collector implementation directly in this focused unit test.
        collector._record_metric("test_counter", 5, "counter")  # pylint: disable=protected-access
        mock_counter.add.assert_called_once_with(5)

    def test_record_metric_gauge(self):
        """Record the latest numeric value on the named gauge."""
        config = Config()
        collector = create_mock_collector(config)

        # Create a mock gauge
        mock_gauge = MagicMock()
        collector.otel_metrics["test_gauge"] = mock_gauge

        # Verify the collector implementation directly in this focused unit test.
        collector._record_metric("test_gauge", 42, "gauge")  # pylint: disable=protected-access
        mock_gauge.set.assert_called_once_with(42)


@pytest.mark.asyncio
class TestSYSMetricsCollectorAsync:
    """Async tests for SYSMetricsCollector."""

    async def test_start_stop_lifecycle(self):
        """Test collector start and stop."""
        config = Config()
        collector = create_mock_collector(config)

        with patch("paho.mqtt.client.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            with patch("mosquitto_exporter.app.start_http_server") as start_http_server:
                # Start collector (will run briefly)
                task = asyncio.create_task(collector.start())
                await asyncio.sleep(0.1)
                task.cancel()
                await task

            start_http_server.assert_called_once_with(config.prometheus.port, addr="0.0.0.0")
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

        # Verify the collector implementation directly in this focused unit test.
        collector._on_connect(mock_client, None, None, mock_reason_code, mock_properties)  # pylint: disable=protected-access

        # Should subscribe to all SYS topics
        assert mock_client.subscribe.call_count == len(SYS_METRICS)


@pytest.mark.asyncio
async def test_malformed_payload_logs_traceback_and_keeps_callback_usable():
    """A corrupt broker payload must not prevent processing the next valid message."""
    config = Config(prometheus=PrometheusConfig(enabled=False))
    collector = create_mock_collector(config)
    with (
        patch("paho.mqtt.client.Client") as client_class,
        patch("mosquitto_exporter.app.asyncio.sleep", side_effect=asyncio.CancelledError),
        patch("mosquitto_exporter.app.logger") as log,
    ):
        await collector.start()
        client = client_class.return_value
        malformed = MQTTMessage(topic=b"$SYS/broker/uptime")
        malformed.payload = b"\xff"
        client.on_message(client, None, malformed)
        assert log.warning.call_args.kwargs["exc_info"] is True
        assert "mosquitto_uptime_seconds" not in collector.metrics_data

        valid = MQTTMessage(topic=b"$SYS/broker/uptime")
        valid.payload = b"3600"
        client.on_message(client, None, valid)
        assert collector.metrics_data["mosquitto_uptime_seconds"] == 3600


@pytest.mark.parametrize(
    ("version", "expected"), [("3", 3), ("5", 5), ("4", None), ("invalid", None), ("5.0", None)]
)
def test_protocol_version_from_environment(monkeypatch, version, expected):
    """Convert supported string versions while rejecting unsupported environment values."""
    monkeypatch.setenv("MQTT_VERSION", version)
    if expected is None:
        with pytest.raises(ValidationError):
            MQTTConfig()
    else:
        assert MQTTConfig().version == expected
