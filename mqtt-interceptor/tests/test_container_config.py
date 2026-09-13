"""Validate the environment syntax passed by the shipped Compose deployment."""

import pytest
from pydantic import ValidationError

from mqtt_interceptor.config import Config, MQTTConfig, TraceConfig


@pytest.mark.parametrize("version", ["3", "5"])
def test_mqtt_version_from_environment(monkeypatch, version):
    """Validate protocol literals supplied through the deployment environment."""
    monkeypatch.setenv("MQTT_VERSION", version)
    assert MQTTConfig().version == int(version)


@pytest.mark.parametrize("version", ["4", "invalid", "5.0"])
def test_unsupported_mqtt_version_is_rejected(monkeypatch, version):
    """Validate protocol literals supplied through the deployment environment."""
    monkeypatch.setenv("MQTT_VERSION", version)
    with pytest.raises(ValidationError):
        MQTTConfig()


def test_compose_environment_loads_before_service_start(monkeypatch):
    """Accept the protocol and CSV topic settings shipped in Compose."""
    monkeypatch.setenv("MQTT_VERSION", "5")
    monkeypatch.setenv("TRACE_TOPIC_PATTERNS", "devices/+/telemetry,vehicles/+/telemetry")
    config = Config()
    assert config.mqtt.version == 5
    assert config.trace.topic_patterns == ["devices/+/telemetry", "vehicles/+/telemetry"]


def test_json_topic_environment_remains_supported(monkeypatch):
    """Retain JSON topic list support alongside Compose CSV settings."""
    monkeypatch.setenv("TRACE_TOPIC_PATTERNS", '["devices/+/telemetry", "vehicles/+/telemetry"]')
    assert TraceConfig().topic_patterns == ["devices/+/telemetry", "vehicles/+/telemetry"]
