"""Real same-port readiness and MQTT connection/freshness boundary tests."""

import gzip
import http.client
import math
from unittest.mock import MagicMock, patch

import pytest
from prometheus_client import Gauge

from mqtt_interceptor.readiness import BrokerReadiness, start_metrics_server


def test_connection_receipt_and_age_boundaries():
    state = BrokerReadiness()
    assert not state.is_ready()
    with patch("mqtt_interceptor.readiness.time.monotonic", return_value=100):
        state.observe(25)  # Receipts outside a successful connection cannot enable it.
        assert not state.is_ready()
        state.connected(True)
        assert not state.is_ready()
        state.observe(0)
        assert state.is_ready()
    for now, expected in ((144.999, True), (145, False), (200, False), (99, False)):
        with patch("mqtt_interceptor.readiness.time.monotonic", return_value=now):
            assert state.is_ready() is expected
    state.connected(False)
    assert not state.is_ready()
    state.connected(True)
    assert not state.is_ready()  # Reconnect must not reuse the prior receipt.
    state.observe(25)
    assert state.is_ready()


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, -1])
def test_invalid_latest_sample_fails_closed(value):
    state = BrokerReadiness()
    state.connected(True)
    state.observe(25)
    assert state.is_ready()
    state.observe(value)
    assert not state.is_ready()


def test_same_port_http_is_cheap_and_preserves_real_prometheus_output():
    state = BrokerReadiness()
    server, thread = start_metrics_server(0, state.is_ready, addr="127.0.0.1")
    metric = Gauge("mqtt_interceptor_readiness_test_value", "Same listener sample", ["broker"])
    metric.labels('cerbo "quoted" \\ path').set(25)

    def request(path, method="GET", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    try:
        # A /ready request never invokes Prometheus collection (which may run
        # costly OTel callbacks) and does not change application state.
        with patch("prometheus_client.exposition._bake_output", side_effect=AssertionError):
            assert request("/ready")[0] == 503
            state.connected(True)
            state.observe(25)
            status, headers, body = request("/ready")
            assert (status, body) == (200, b"ready\n")
            assert headers["Cache-Control"] == "no-store"
            assert request("/ready", method="POST")[0] == 405
            state.connected(False)
            assert request("/ready")[0] == 503
        status, headers, body = request(
            "/metrics?name[]=mqtt_interceptor_readiness_test_value",
            headers={"Accept-Encoding": "gzip"},
        )
        assert status == 200 and headers["Content-Encoding"] == "gzip"
        body = gzip.decompress(body)
        assert b'mqtt_interceptor_readiness_test_value{broker="cerbo' in body
        assert b" 25.0\n" in body
        assert b"python_gc" not in body  # Existing restricted-registry query is preserved.
    finally:
        from prometheus_client import REGISTRY

        REGISTRY.unregister(metric)
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    assert not thread.is_alive()


def test_observer_requires_successful_session_and_matching_receipt():
    from paho.mqtt.client import MQTTMessage
    from mqtt_interceptor.app import MQTTInterceptor
    from mqtt_interceptor.config import Config

    config = Config()
    config.trace.topic_patterns = ["$SYS/broker/load/messages/received/total"]
    with patch.object(MQTTInterceptor, "_setup_tracing"):
        observer = MQTTInterceptor(config)
    client = MagicMock()
    observer._on_connect(client, None, None, 135, None)
    client.subscribe.assert_not_called()
    assert not observer.readiness.is_ready()
    observer._on_connect(client, None, None, 0, None)
    client.subscribe.assert_called_once_with(config.trace.topic_patterns[0], qos=2)
    message = MQTTMessage(topic=b"unrelated/topic")
    message.payload = b"arbitrary nonnumeric application data"
    observer._handle_publish(message)
    assert not observer.readiness.is_ready()
    message.topic = config.trace.topic_patterns[0].encode()
    observer._handle_publish(message)
    assert observer.readiness.is_ready()
    observer._on_disconnect(client, None, None, 0, None)
    assert not observer.readiness.is_ready()
    observer._on_connect(client, None, None, 0, None)
    assert not observer.readiness.is_ready()
    client.publish.assert_not_called()
