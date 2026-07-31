from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MQTTConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MQTT_", extra="ignore")

    upstream_host: str = "mosquitto"
    upstream_port: int = 1883
    username: str | None = None
    password: str | None = None
    client_id: str = "mosquitto-exporter"
    keepalive: int = 60
    version: Literal[3, 5] = 5
    reconnect_delay: int = 5
    tls_enabled: bool = False
    tls_ca_cert: str | None = None
    tls_certfile: str | None = None
    tls_keyfile: str | None = None


class OTelConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OTEL_", extra="ignore")

    endpoint: str = "http://otelcol:4317"
    service_name: str = "mosquitto-exporter"
    resource_attributes: dict[str, str] = Field(default_factory=dict)
    insecure: bool = True
    timeout: int = 10
    headers: dict[str, str] = {}
    export_interval: int = 30

    @property
    def export_interval_ms(self) -> int:
        return self.export_interval * 1000


class PrometheusConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROMETHEUS_", extra="ignore")

    enabled: bool = True
    port: int = 9494
    path: str = "/metrics"


class MetricsConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="METRICS_", extra="ignore")

    enabled: bool = True
    prometheus_enabled: bool = True
    otel_enabled: bool = True
    export_interval: int = 30
    scrape_interval: int = 10
    stale_threshold: int = 120
    sys_topics: list[str] = Field(
        default_factory=lambda: [
            "$SYS/broker/uptime",
            "$SYS/broker/clients/connected",
            "$SYS/broker/clients/expired",
            "$SYS/broker/clients/disconnected",
            "$SYS/broker/messages/received",
            "$SYS/broker/messages/sent",
            "$SYS/broker/messages/dropped",
            "$SYS/broker/messages/inflight",
            "$SYS/broker/bytes/received",
            "$SYS/broker/bytes/sent",
            "$SYS/broker/subscriptions/count",
        ]
    )


class LoggingConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOG_", extra="ignore")

    level: str = "INFO"
    format: Literal["json", "console"] = "json"


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    mqtt: MQTTConfig = Field(default_factory=MQTTConfig)
    otel: OTelConfig = Field(default_factory=OTelConfig)
    prometheus: PrometheusConfig = Field(default_factory=PrometheusConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


_config: Config | None = None


def load_config(config_path: str | None = None) -> Config:
    global _config
    if _config is not None:
        return _config

    if config_path and Path(config_path).exists():
        _config = Config(_env_file=config_path)  # type: ignore[call-arg]
    else:
        _config = Config()
    return _config


def get_config() -> Config:
    if _config is None:
        return load_config()
    return _config
