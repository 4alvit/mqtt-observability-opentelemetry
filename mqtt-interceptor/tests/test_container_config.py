"""Validate the environment syntax passed by the shipped Compose deployment."""

import pytest
from pydantic import ValidationError

from mqtt_interceptor.config import Config, MQTTConfig, TraceConfig


@pytest.mark.parametrize("version", ["3", "5"])
def test_mqtt_version_from_environment(monkeypatch, version):
    monkeypatch.setenv("MQTT_VERSION", version)
    assert MQTTConfig().version == int(version)


def test_unsupported_mqtt_version_is_rejected(monkeypatch):
    monkeypatch.setenv("MQTT_VERSION", "4")
    with pytest.raises(ValidationError):
        MQTTConfig()


def test_compose_environment_loads_before_service_start(monkeypatch):
    monkeypatch.setenv("MQTT_VERSION", "5")
    monkeypatch.setenv("TRACE_TOPIC_PATTERNS", "devices/+/telemetry,vehicles/+/telemetry")
    config = Config()
    assert config.mqtt.version == 5
    assert config.trace.topic_patterns == ["devices/+/telemetry", "vehicles/+/telemetry"]


def test_json_topic_environment_remains_supported(monkeypatch):
    monkeypatch.setenv("TRACE_TOPIC_PATTERNS", '["devices/+/telemetry", "vehicles/+/telemetry"]')
    assert TraceConfig().topic_patterns == ["devices/+/telemetry", "vehicles/+/telemetry"]
