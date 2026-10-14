"""Real Paho handshakes must reject weak trust chains before MQTT CONNECT."""

import socket
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
import pytest
from test_otlp_tls import CHAIN_CASES, chains as chains

from mosquitto_exporter.mqtt_tls import broker_tls_context


@contextmanager
def broker_peer(
    chain: tuple[Path, Path, Path], version: ssl.TLSVersion
) -> Iterator[tuple[int, dict[str, Any]]]:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = context.maximum_version = version
    # Deliberately weak fixture: prove rejection is in the product, not the server.
    context.set_ciphers("DEFAULT:@SECLEVEL=0")
    context.load_cert_chain(chain[0], chain[1])
    result: dict[str, Any] = {"application_bytes": b""}
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)

        def accept_one() -> None:
            try:
                raw, _ = listener.accept()
                with raw:
                    raw.settimeout(5)
                    with context.wrap_socket(raw, server_side=True) as connection:
                        result["application_bytes"] = connection.recv(8192)
            except (ssl.SSLError, ConnectionResetError):
                pass
            except Exception as error:
                result["error"] = repr(error)

        worker = threading.Thread(target=accept_one)
        worker.start()
        try:
            yield listener.getsockname()[1], result
        finally:
            worker.join(7)
            assert not worker.is_alive()
            assert "error" not in result, result


@pytest.mark.parametrize("case", CHAIN_CASES)
@pytest.mark.parametrize("version", [ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3])
def test_paho_rejects_weak_chain_before_mqtt_credentials(chains, case, version):
    chain = chains[case]
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set("synthetic-user", "synthetic-mqtt-token")
    client.tls_set_context(broker_tls_context(str(chain[2])))
    with broker_peer(chain, version) as (port, result):
        if case.startswith("weak"):
            with pytest.raises(ssl.SSLError):
                client.connect("localhost", port)
        else:
            assert client.connect("localhost", port) == mqtt.MQTT_ERR_SUCCESS
            client.disconnect()
    if case.startswith("weak"):
        assert result["application_bytes"] == b""
    else:
        assert b"synthetic-mqtt-token" in result["application_bytes"]


def test_paho_checks_hostname_before_credentials(chains):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set("synthetic-user", "synthetic-mqtt-token")
    client.tls_set_context(broker_tls_context(str(chains["strong"][2])))
    with broker_peer(chains["strong"], ssl.TLSVersion.TLSv1_2) as (port, result):
        with pytest.raises(ssl.SSLError):
            client.connect("127.0.0.1", port)
    assert result["application_bytes"] == b""


@pytest.mark.parametrize("case", ["weak-leaf", "weak-intermediate"])
def test_rejects_weak_client_chain_before_connecting(chains, case):
    certificate, key, ca = chains[case]
    with pytest.raises(ssl.SSLError):
        broker_tls_context(str(ca), str(certificate), str(key))
