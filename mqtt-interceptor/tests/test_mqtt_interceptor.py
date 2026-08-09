"""Tests for MQTT Interceptor."""

import os
from unittest.mock import AsyncMock, patch

import paho.mqtt.client as mqtt
import pytest

from mqtt_interceptor import (
    MQTTInterceptor,
    TopicSpanProcessor,
    TraceContextPropagator,
)
from mqtt_interceptor.config import (
    Config,
    MQTTConfig,
    OTELConfig,
    TraceConfig,
    load_config,
)


class TestTraceContextPropagator:
    """Tests for W3C Trace Context propagation."""

    def test_extract_from_user_properties(self):
        """Test extracting trace context from user properties."""
        propagator = TraceContextPropagator("w3c")
        user_properties = [
            ("traceparent", "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"),
            ("tracestate", "congo=t61rcWkgMzE"),
        ]

        span_context = propagator.extract(None, user_properties)

        assert span_context is not None
        assert span_context.trace_id == 0x0af7651916cd43dd8448eb211c80319c
        assert span_context.span_id == 0xb7ad6b7169203331
        assert span_context.trace_flags == 0x01

    def test_extract_from_mqtt5_properties(self):
        """Test extracting trace context from MQTT v5 properties."""
        propagator = TraceContextPropagator("w3c")
        properties = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
        properties.UserProperty = [
            ("traceparent", "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"),
        ]

        span_context = propagator.extract(properties, None)

        assert span_context is not None
        assert span_context.trace_id == 0x0af7651916cd43dd8448eb211c80319c

    def test_extract_returns_none_when_missing(self):
        """Test extraction returns None when trace context is missing."""
        propagator = TraceContextPropagator("w3c")
        span_context = propagator.extract(None, None)
        assert span_context is None

    def test_extract_handles_invalid_traceparent(self):
        """Test extraction handles malformed traceparent."""
        propagator = TraceContextPropagator("w3c")
        user_properties = [("traceparent", "invalid-format")]
        span_context = propagator.extract(None, user_properties)
        assert span_context is None

    def test_inject_trace_context(self):
        """Test injecting trace context into message properties."""
        propagator = TraceContextPropagator("w3c")

        from opentelemetry.trace import SpanContext, TraceFlags, TraceState

        span_context = SpanContext(
            trace_id=0x0af7651916cd43dd8448eb211c80319c,
            span_id=0xb7ad6b7169203331,
            is_remote=True,
            trace_flags=TraceFlags(0x01),
            trace_state=TraceState(),
        )

        properties, user_properties = propagator.inject(None, [], span_context)

        assert user_properties is not None
        traceparent_found = False
        for k, v in user_properties:
            if k == "traceparent":
                assert v == "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
                traceparent_found = True
        assert traceparent_found


class TestTopicSpanProcessor:
    """Tests for topic-based span creation."""

    def test_matches_exact_topic(self):
        """Test pattern matching with exact topic."""
        from opentelemetry import trace
        tracer = trace.get_tracer(__name__)
        processor = TopicSpanProcessor(tracer, ["devices/sensor-001/telemetry"], 1.0)

        matched, pattern = processor.matches("devices/sensor-001/telemetry")
        assert matched is True
        assert pattern == "devices/sensor-001/telemetry"

    def test_matches_wildcard_plus(self):
        """Test pattern matching with + wildcard."""
        from opentelemetry import trace
        tracer = trace.get_tracer(__name__)
        processor = TopicSpanProcessor(tracer, ["devices/+/telemetry"], 1.0)

        matched, pattern = processor.matches("devices/sensor-001/telemetry")
        assert matched is True
        assert pattern == "devices/+/telemetry"

    def test_matches_wildcard_hash(self):
        """Test pattern matching with # wildcard."""
        from opentelemetry import trace
        tracer = trace.get_tracer(__name__)
        processor = TopicSpanProcessor(tracer, ["devices/#"], 1.0)

        matched, pattern = processor.matches("devices/sensor-001/telemetry/temperature")
        assert matched is True
        assert pattern == "devices/#"

    def test_no_match(self):
        """Test non-matching topic returns False."""
        from opentelemetry import trace
        tracer = trace.get_tracer(__name__)
        processor = TopicSpanProcessor(tracer, ["devices/+/telemetry"], 1.0)

        matched, pattern = processor.matches("other/topic")
        assert matched is False
        assert pattern is None

    def test_extract_attributes_from_wildcard(self):
        """Test attribute extraction from wildcard topics."""
        from opentelemetry import trace
        tracer = trace.get_tracer(__name__)
        processor = TopicSpanProcessor(tracer, ["devices/+/telemetry"], 1.0)

        attrs = processor.extract_attributes("devices/sensor-001/telemetry", "devices/+/telemetry")

        assert attrs["mqtt.topic"] == "devices/sensor-001/telemetry"
        assert attrs["mqtt.topic_pattern"] == "devices/+/telemetry"
        assert attrs["mqtt.topic_segment_1"] == "sensor-001"

    def test_should_sample_zero_rate(self):
        """Test sampling with rate 0 returns False."""
        from opentelemetry import trace
        tracer = trace.get_tracer(__name__)
        processor = TopicSpanProcessor(tracer, ["devices/+/telemetry"], 0.0)

        for _ in range(100):
            assert processor.should_sample() is False

    def test_should_sample_full_rate(self):
        """Test sampling with rate 1 returns True."""
        from opentelemetry import trace
        tracer = trace.get_tracer(__name__)
        processor = TopicSpanProcessor(tracer, ["devices/+/telemetry"], 1.0)

        for _ in range(10):
            assert processor.should_sample() is True


class TestMQTTConfig:
    """Tests for MQTT configuration."""

    def test_default_values(self):
        """Test default configuration values."""
        config = MQTTConfig()
        assert config.upstream_host == "mosquitto"
        assert config.upstream_port == 1883
        assert config.version == 5
        assert config.client_id == "mqtt-interceptor"
        assert config.clean_start is True

    def test_custom_values_via_env(self, monkeypatch):
        """Test custom configuration via environment variables."""
        monkeypatch.setenv("MQTT_UPSTREAM_HOST", "broker")
        monkeypatch.setenv("MQTT_UPSTREAM_PORT", "1884")

        config = MQTTConfig()
        assert config.upstream_host == "broker"
        assert config.upstream_port == 1884
        assert config.upstream_address == "broker:1884"


class TestTraceConfig:
    """Tests for trace configuration."""

    def test_default_topic_patterns(self):
        """Test default topic patterns."""
        config = TraceConfig()
        assert config.topic_patterns == ["devices/+/telemetry", "devices/+/commands"]

    def test_parse_topic_patterns_from_string(self):
        """Test parsing topic patterns from comma-separated string."""
        config = TraceConfig(topic_patterns="test/+/topic1, test/+/topic2")
        assert config.topic_patterns == ["test/+/topic1", "test/+/topic2"]

    def test_sample_rate_via_env(self, monkeypatch):
        """Test sample rate via environment variable."""
        monkeypatch.setenv("TRACE_SAMPLE_RATE", "0.5")
        config = TraceConfig()
        assert config.sample_rate == 0.5

    def test_sample_rate_validation(self):
        """Test sample rate bounds validation (default)."""
        config = TraceConfig()
        assert config.sample_rate == 0.1

    def test_invalid_sample_rate_via_env(self, monkeypatch):
        """Test invalid sample rate via env raises error."""
        monkeypatch.setenv("TRACE_SAMPLE_RATE", "1.5")
        with pytest.raises(ValueError):
            TraceConfig()

        monkeypatch.setenv("TRACE_SAMPLE_RATE", "-0.1")
        with pytest.raises(ValueError):
            TraceConfig()


class TestOTELConfig:
    """Tests for OTEL configuration."""

    def test_parse_resource_attributes_via_env(self, monkeypatch):
        """Test parsing resource attributes from JSON string via env."""
        # With env_prefix="OTEL_", the env var is OTEL_RESOURCE_ATTRIBUTES
        monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", '{"key1":"value1","key2":"value2"}')
        config = OTELConfig()
        assert config.resource_attributes == {"key1": "value1", "key2": "value2"}

    def test_default_endpoint(self):
        """Test default OTEL endpoint."""
        config = OTELConfig()
        assert config.endpoint == "http://otelcol:4317"


class TestConfigLoading:
    """Tests for configuration loading."""

    def test_load_config_defaults(self):
        """Test loading configuration with defaults."""
        config = load_config()
        assert isinstance(config, Config)
        assert config.mqtt.upstream_host == "mosquitto"
        assert config.trace.sample_rate == 0.1

    def test_load_config_from_yaml(self, tmp_path, monkeypatch):
        """Test loading configuration from YAML file."""
        yaml_content = """
mqtt:
  upstream_host: "test-broker"
  upstream_port: 1884
trace:
  sample_rate: 0.5
  topic_patterns:
    - "test/+/topic"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)

        # Ensure no env vars interfere
        for key in list(os.environ.keys()):
            if key.startswith(('MQTT_', 'TRACE_', 'OTEL_', 'METRICS_', 'LOG_')):
                monkeypatch.delenv(key, raising=False)

        config = load_config(config_file)
        assert config.mqtt.upstream_host == "test-broker"
        assert config.mqtt.upstream_port == 1884
        assert config.trace.sample_rate == 0.5
        assert config.trace.topic_patterns == ["test/+/topic"]


class TestMQTTInterceptor:
    """Tests for MQTT Interceptor main class."""

    @pytest.mark.asyncio
    async def test_interceptor_creation(self):
        """Test creating interceptor with config."""
        config = Config()
        interceptor = MQTTInterceptor(config)

        assert interceptor.config == config
        assert interceptor.running is False
        assert interceptor.client is None
        assert interceptor.propagator is not None
        assert interceptor.tracer is not None
        assert interceptor.span_processor is not None

    @pytest.mark.asyncio
    async def test_interceptor_start_stop(self):
        """Test interceptor start and stop lifecycle."""
        config = Config()

        with patch("paho.mqtt.client.Client") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            interceptor = MQTTInterceptor(config)

            with patch("prometheus_client.start_http_server"):
                await interceptor.start()
                assert interceptor.running is True
                mock_client.connect.assert_called_once()
                mock_client.loop_start.assert_called_once()

            await interceptor.stop()
            assert interceptor.running is False
            mock_client.loop_stop.assert_called_once()
            mock_client.disconnect.assert_called_once()


@pytest.fixture
def sample_interceptor():
    """Fixture providing a configured interceptor for testing."""
    config = Config()
    return MQTTInterceptor(config)
