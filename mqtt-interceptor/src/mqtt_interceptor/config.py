"""Validated environment and YAML configuration for the MQTT interceptor."""

import json
from pathlib import Path
from typing import Annotated, Literal, cast

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from mqtt_interceptor._version import __version__


class MQTTConfig(BaseSettings):
    """Broker connection settings and supported MQTT protocol versions."""

    model_config = SettingsConfigDict(env_prefix="MQTT_", extra="ignore")

    upstream_host: str = Field(default="mosquitto")
    upstream_port: int = Field(default=1883)
    version: Literal[3, 5] = Field(default=5)
    client_id: str = Field(default="mqtt-interceptor")
    username: str | None = Field(default=None)
    password: str | None = Field(default=None)
    keepalive: int = Field(default=60)
    clean_start: bool = Field(default=True)

    @field_validator("version", mode="before")
    @classmethod
    def parse_version(cls, value: int | str) -> int | str:
        """Normalize environment strings before supported-version validation."""
        # Environment variables are strings; retain the supported-version validation.
        return int(value) if value in ("3", "5") else value

    @property
    def upstream_address(self) -> str:
        """Return the configured broker host and port."""
        return f"{self.upstream_host}:{self.upstream_port}"


class TraceConfig(BaseSettings):
    """Trace sampling and topic selection settings."""

    model_config = SettingsConfigDict(env_prefix="TRACE_", extra="ignore")

    propagator: Literal["w3c", "baggage", "mqtt-topic"] = Field(default="w3c")
    topic_patterns: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["devices/+/telemetry", "devices/+/commands"]
    )
    sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)
    propagate_on_publish: bool = Field(default=True)
    propagate_on_subscribe: bool = Field(default=True)
    span_attributes: dict[str, str] = Field(default_factory=dict)

    @field_validator("topic_patterns", mode="before")
    @classmethod
    def parse_topic_patterns(cls, v: str | list[str]) -> list[str]:
        """Accept explicit JSON arrays or comma-separated topic lists."""
        if isinstance(v, str):
            if v.lstrip().startswith("["):
                return cast(list[str], json.loads(v))
            return [p.strip() for p in v.split(",") if p.strip()]
        return v


class OTELConfig(BaseSettings):
    """OpenTelemetry exporter and resource settings."""

    model_config = SettingsConfigDict(env_prefix="OTEL_", extra="ignore")

    endpoint: str = Field(default="http://otelcol:4317")
    service_name: str = Field(default="mqtt-interceptor")
    service_version: str = Field(default=__version__)
    resource_attributes: dict[str, str] = Field(default={})
    insecure: bool = Field(default=True)
    timeout: int = Field(default=10)
    headers: dict[str, str] = Field(default={})

    @field_validator("resource_attributes", mode="before")
    @classmethod
    def parse_resource_attributes(cls, v: str | dict[str, str]) -> dict[str, str]:
        """Decode JSON objects or comma-separated resource attributes."""
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            # Try JSON first
            try:
                return cast(dict[str, str], json.loads(v))
            except json.JSONDecodeError:
                pass
            # Fall back to comma-separated key=value format
            result = {}
            for pair in v.split(","):
                if "=" in pair:
                    k, v_val = pair.split("=", 1)
                    result[k.strip()] = v_val.strip()
            return result
        return v


class MetricsConfig(BaseSettings):
    """Prometheus listener settings."""

    model_config = SettingsConfigDict(env_prefix="METRICS_", extra="ignore")

    enabled: bool = Field(default=True)
    port: int = Field(default=9464)
    path: str = Field(default="/metrics")


class LoggingConfig(BaseSettings):
    """Structured logging settings."""

    model_config = SettingsConfigDict(env_prefix="LOG_", extra="ignore")

    level: str = Field(default="INFO")
    format: Literal["json", "console"] = Field(default="json")
    level_styles: dict[str, str] = Field(default={})


class Config(BaseSettings):
    """Complete interceptor configuration from environment or YAML."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mqtt: MQTTConfig = Field(default_factory=MQTTConfig)
    trace: TraceConfig = Field(default_factory=TraceConfig)
    otel: OTELConfig = Field(default_factory=OTELConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Config":
        """Read and validate a UTF-8 YAML configuration file."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)


def load_config(config_path: str | Path | None = None) -> Config:
    """Read an explicit existing YAML file or use environment defaults."""
    if config_path and Path(config_path).exists():
        return Config.from_yaml(config_path)
    return Config()


__all__ = [
    "Config",
    "MQTTConfig",
    "TraceConfig",
    "OTELConfig",
    "MetricsConfig",
    "LoggingConfig",
    "load_config",
]
