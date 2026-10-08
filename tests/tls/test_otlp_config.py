"""Component configuration remains explicit across the OTLP protocol migration."""

import importlib
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


@pytest.fixture(params=["mqtt_interceptor", "mosquitto_exporter"])
def component(request, monkeypatch):
    for name in os.environ:
        if name.startswith("OTEL_"):
            monkeypatch.delenv(name)
    return request.param


def signal(component):
    return "TRACES" if component == "mqtt_interceptor" else "METRICS"


@pytest.mark.parametrize("value", [None, "", "  "])
def test_local_grpc_default_and_existing_timeout_are_retained(
    component, monkeypatch, value
):
    transport = importlib.import_module(component + ".otlp")
    if value is not None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", value)
    kind = "trace" if component == "mqtt_interceptor" else "metric"
    cls = "OTLPSpanExporter" if kind == "trace" else "OTLPMetricExporter"
    with patch(
        f"opentelemetry.exporter.otlp.proto.grpc.{kind}_exporter.{cls}"
    ) as exporter:
        assert (
            transport.create_exporter("http://otelcol:4317", True, 23)
            is exporter.return_value
        )
    exporter.assert_called_once_with(
        endpoint="http://otelcol:4317", insecure=True, timeout=23
    )


@pytest.mark.parametrize(
    "endpoint,insecure", [("https://collector:4317", True), ("collector:4317", False)]
)
def test_tls_grpc_is_rejected_before_provider_registration(
    component, endpoint, insecure
):
    app = importlib.import_module(component + ".app")
    config = SimpleNamespace(
        otel=SimpleNamespace(
            endpoint=endpoint,
            insecure=insecure,
            timeout=10,
            service_name="test",
            service_version="test",
            resource_attributes={},
            export_interval_ms=17000,
        ),
        prometheus=SimpleNamespace(enabled=False),
    )
    if component == "mqtt_interceptor":
        obj = app.MQTTInterceptor.__new__(app.MQTTInterceptor)
        obj.config = config
        with (
            patch.object(app.trace, "set_tracer_provider") as setter,
            pytest.raises(ValueError, match="TLS gRPC cannot enforce"),
        ):
            obj._setup_tracing()
    else:
        obj = app.SYSMetricsCollector.__new__(app.SYSMetricsCollector)
        obj.config = config
        with (
            patch.object(app.metrics, "set_meter_provider") as setter,
            pytest.raises(ValueError, match="TLS gRPC cannot enforce"),
        ):
            obj._setup_otel()
    setter.assert_not_called()


@pytest.mark.parametrize("specific", [False, True])
def test_signal_endpoint_precedence_headers_and_component_timeout(
    component, monkeypatch, specific
):
    transport = importlib.import_module(component + ".otlp")
    name = signal(component)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    monkeypatch.setenv(f"OTEL_EXPORTER_OTLP_{name}_PROTOCOL", "  ")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://ignored-global.example")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "99")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "x-common=common")
    monkeypatch.setenv(f"OTEL_EXPORTER_OTLP_{name}_HEADERS", "x-signal=selected")
    if specific:
        monkeypatch.setenv(
            f"OTEL_EXPORTER_OTLP_{name}_ENDPOINT", "https://signal.example/exact"
        )
    exporter = transport.create_exporter("https://component.example/prefix/", False, 7)
    try:
        expected = (
            "https://signal.example/exact"
            if specific
            else ("https://component.example/prefix/v1/" + name.lower())
        )
        assert exporter._endpoint == expected
        assert exporter._timeout == 7
        assert exporter._session.headers["x-signal"] == "selected"
        assert "x-common" not in exporter._session.headers
    finally:
        exporter.shutdown()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://collector:4318",
        "https://u:p@collector",
        "https://collector/#x",
        "https://",
    ],
)
def test_http_protocol_requires_final_verified_https_endpoint(
    component, monkeypatch, endpoint
):
    monkeypatch.setenv(
        f"OTEL_EXPORTER_OTLP_{signal(component)}_PROTOCOL", "http/protobuf"
    )
    transport = importlib.import_module(component + ".otlp")
    with pytest.raises(ValueError, match="requires an HTTPS endpoint"):
        transport.create_exporter(endpoint)


def test_signal_protocol_overrides_common_and_unknown_protocol_fails(
    component, monkeypatch
):
    transport = importlib.import_module(component + ".otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    monkeypatch.setenv(
        f"OTEL_EXPORTER_OTLP_{signal(component)}_PROTOCOL", "http/protobuf"
    )
    exporter = transport.create_exporter("https://collector.example")
    exporter.shutdown()
    monkeypatch.setenv(f"OTEL_EXPORTER_OTLP_{signal(component)}_PROTOCOL", "invalid")
    with pytest.raises(ValueError, match="must be grpc or http/protobuf"):
        transport.create_exporter("https://collector.example")


def test_metrics_reader_interval_and_prometheus_coexistence(monkeypatch):
    from mosquitto_exporter import app

    obj = app.SYSMetricsCollector.__new__(app.SYSMetricsCollector)
    obj.config = SimpleNamespace(
        otel=SimpleNamespace(
            endpoint="http://otelcol:4317",
            insecure=True,
            timeout=7,
            service_name="test",
            resource_attributes={},
            export_interval_ms=17000,
        ),
        prometheus=SimpleNamespace(enabled=True),
    )
    obj._create_otel_metrics = Mock()
    with (
        patch.object(app, "create_exporter") as factory,
        patch.object(app, "PrometheusMetricReader") as prometheus,
        patch.object(app, "PeriodicExportingMetricReader") as periodic,
        patch.object(app, "MeterProvider") as provider,
        patch.object(app.metrics, "set_meter_provider"),
        patch.object(app.metrics, "get_meter"),
    ):
        obj._setup_otel()
    factory.assert_called_once_with(
        endpoint="http://otelcol:4317", insecure=True, timeout=7
    )
    periodic.assert_called_once_with(factory.return_value, export_interval_millis=17000)
    assert provider.call_args.kwargs["metric_readers"] == [
        prometheus.return_value,
        periodic.return_value,
    ]
    obj._create_otel_metrics.assert_called_once_with()
