"""MQTT Interceptor - MQTT message interception with OpenTelemetry tracing."""

from mqtt_interceptor.app import (
    MQTTInterceptor,
    TopicSpanProcessor,
    TraceContextPropagator,
)
from mqtt_interceptor.config import (
    Config,
    LoggingConfig,
    MetricsConfig,
    MQTTConfig,
    OTELConfig,
    TraceConfig,
    load_config,
)

# Public convenience exports intentionally match config.__all__.
# pylint: disable=duplicate-code
__all__ = [
    "MQTTInterceptor",
    "TopicSpanProcessor",
    "TraceContextPropagator",
    "Config",
    "MQTTConfig",
    "TraceConfig",
    "OTELConfig",
    "MetricsConfig",
    "LoggingConfig",
    "load_config",
]
# pylint: enable=duplicate-code

__version__ = "0.2.1"
