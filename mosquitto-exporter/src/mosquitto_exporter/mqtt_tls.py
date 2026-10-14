"""Verify the broker chain before Paho transmits MQTT credentials or payloads."""

import ssl

from mosquitto_exporter.otlp import _client_tls_context, _tls_context, _verify_key_lengths


class _BrokerSocket(ssl.SSLSocket):
    def do_handshake(self, block: bool = False) -> None:
        super().do_handshake(block)
        try:
            _verify_key_lengths(self)
        except Exception:
            self.close()
            raise


def broker_tls_context(
    ca_file: str | None = None, cert_file: str | None = None, key_file: str | None = None
) -> ssl.SSLContext:
    """Retain configured trust roots and reject undersized peer/client keys."""
    if key_file and not cert_file:
        raise ValueError("MQTT TLS client key requires a client certificate")
    context = _client_tls_context(cert_file, key_file) if cert_file else _tls_context()
    if ca_file:
        context.load_verify_locations(cafile=ca_file)
    else:
        context.load_default_certs()
    context.sslsocket_class = _BrokerSocket
    return context
