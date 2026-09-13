"""MQTT Interceptor - MQTT message interception with OpenTelemetry tracing."""

# Explicit re-export retains the existing package attribute.
# pylint: disable-next=useless-import-alias
from mqtt_interceptor._version import __version__ as __version__
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
