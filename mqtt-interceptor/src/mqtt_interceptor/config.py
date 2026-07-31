from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MQTTConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MQTT_", extra="ignore")

    listen_host: str = Field(default="0.0.0.0", alias="LISTEN_HOST")
    listen_port: int = Field(default=1884, alias="LISTEN_PORT")
    upstream_host: str = Field(default="mosquitto", alias="UPSTREAM_HOST")
    upstream_port: int = Field(default=1883, alias="UPSTREAM_PORT")
    version: Literal[3, 5] = Field(default=5, alias="VERSION")
    client_id: str = Field(default="mqtt-interceptor", alias="CLIENT_ID")
    username: str | None = Field(default=None, alias="USERNAME")
    password: str | None = Field(default=None, alias="PASSWORD")
    keepalive: int = Field(default=60, alias="KEEPALIVE")
    clean_start: bool = Field(default=True, alias="CLEAN_START")

    @property
    def listen_address(self) -> str:
        return f"{self.listen_host}:{self.listen_port}"

    @property
    def upstream_address(self) -> str:
        return f"{self.upstream_host}:{self.upstream_port}"


class TraceConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRACE_", extra="ignore")

    propagator: Literal["w3c", "baggage", "mqtt-topic"] = Field(default="w3c", alias="PROPAGATOR")
    topic_patterns: list[str] = Field(
        default_factory=lambda: ["devices/+/telemetry", "devices/+/commands"],
        alias="TOPIC_PATTERNS",
    )
    sample_rate: float = Field(default=0.1, ge=0.0, le=1.0, alias="SAMPLE_RATE")
    propagate_on_publish: bool = Field(default=True, alias="PROPAGATE_ON_PUBLISH")
    propagate_on_subscribe: bool = Field(default=True, alias="PROPAGATE_ON_SUBSCRIBE")
    span_attributes: dict[str, str] = Field(default_factory=dict, alias="SPAN_ATTRIBUTES")

    @field_validator("topic_patterns", mode="before")
    @classmethod
    def parse_topic_patterns(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v


class OTELConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OTEL_", extra="ignore")

    endpoint: str = Field(default="http://otelcol:4317", alias="EXPORTER_OTLP_ENDPOINT")
    service_name: str = Field(default="mqtt-interceptor", alias="SERVICE_NAME")
    service_version: str = Field(default="0.1.0", alias="SERVICE_VERSION")
    resource_attributes: dict[str, str] = Field(default_factory=dict, alias="RESOURCE_ATTRIBUTES")
    insecure: bool = Field(default=True, alias="EXPORTER_OTLP_INSECURE")
    timeout: int = Field(default=10, alias="EXPORTER_OTLP_TIMEOUT")
    headers: dict[str, str] = Field(default_factory=dict, alias="EXPORTER_OTLP_HEADERS")

    @field_validator("resource_attributes", mode="before")
    @classmethod
    def parse_resource_attributes(cls, v: str | dict[str, str]) -> dict[str, str]:
        if isinstance(v, str):
            result = {}
            for pair in v.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    result[k.strip()] = v.strip()
            return result
        return v


class MetricsConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="METRICS_", extra="ignore")

    enabled: bool = Field(default=True, alias="ENABLED")
    port: int = Field(default=9464, alias="PORT")
    path: str = Field(default="/metrics", alias="PATH")


class LoggingConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOG_", extra="ignore")

    level: str = Field(default="INFO", alias="LEVEL")
    format: Literal["json", "console"] = Field(default="json", alias="FORMAT")
    level_styles: dict[str, str] = Field(default_factory=dict, alias="LEVEL_STYLES")


class Config(BaseSettings):
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
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)


def load_config(config_path: str | Path | None = None) -> Config:
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
