"""Mosquitto Exporter - Prometheus/OpenTelemetry metrics exporter for Mosquitto MQTT broker."""

# Explicit re-export retains the existing package attribute.
# pylint: disable-next=useless-import-alias
from mosquitto_exporter._version import __version__ as __version__
from mosquitto_exporter.app import (
    SYS_METRICS,
    SYSMetric,
    SYSMetricsCollector,
)
from mosquitto_exporter.config import (
    Config,
    LoggingConfig,
    MetricsConfig,
    MQTTConfig,
    OTelConfig,
    PrometheusConfig,
    get_config,
    load_config,
)

__all__ = [
    "SYSMetric",
    "SYS_METRICS",
    "SYSMetricsCollector",
    "Config",
    "MQTTConfig",
    "OTelConfig",
    "PrometheusConfig",
    "MetricsConfig",
    "LoggingConfig",
    "load_config",
    "get_config",
]
