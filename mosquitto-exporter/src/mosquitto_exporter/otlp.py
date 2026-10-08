"""Verified OTLP HTTPS transport; preserve explicit local plaintext gRPC."""

# Adapted from victron-venus/venus-os-observability, MIT license.
# MIT License
#
# Copyright (c) 2026 victron-venus
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
# Kept within each independently packaged component so its wheel is self-contained.

import os
import socket
import ssl
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

import requests
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed448, ed25519, rsa
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool

if TYPE_CHECKING:
    from opentelemetry.sdk.metrics.export import MetricExporter
    from requests.adapters import _HostParams, _PoolKwargs


def _tls_context() -> ssl.SSLContext:
    # Leave trust roots to Requests, including explicit OTLP CA files/directories.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if context.minimum_version < ssl.TLSVersion.TLSv1_2:
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    if context.security_level < 2:
        selected = [
            cipher["name"] for cipher in context.get_ciphers() if cipher["protocol"] != "TLSv1.3"
        ]
        context.set_ciphers(":".join([*selected, "@SECLEVEL=2"]))
    return context


def _certificate_from_verified_item(item: object) -> x509.Certificate:
    if isinstance(item, bytes):
        return x509.load_der_x509_certificate(item)
    # CPython 3.12's internal Certificate returns PEM; public 3.13+ returns DER above.
    encode = getattr(item, "public_bytes", None)
    if callable(encode):
        pem = encode()
        if isinstance(pem, str):
            return x509.load_pem_x509_certificate(pem.encode("ascii"))
    raise ssl.SSLError("OTLP TLS runtime cannot expose a verified certificate")


def _key_is_strong(certificate: x509.Certificate) -> bool:
    key = certificate.public_key()
    if isinstance(key, rsa.RSAPublicKey):
        return key.public_numbers().n.bit_length() >= 2048
    if isinstance(key, ec.EllipticCurvePublicKey):
        return key.key_size >= 224
    if isinstance(key, dsa.DSAPublicKey):
        parameters = key.public_numbers().parameter_numbers
        return parameters.p.bit_length() >= 2048 and parameters.q.bit_length() >= 224
    return isinstance(key, ed25519.Ed25519PublicKey | ed448.Ed448PublicKey)


def _verify_key_lengths(sock: object) -> None:
    # SSLTransport (HTTPS proxy tunnel) exposes its inner SSLObject as sslobj.
    tls = getattr(sock, "sslobj", sock)
    get_chain = getattr(tls, "get_verified_chain", None)
    if not callable(get_chain):
        get_chain = getattr(getattr(tls, "_sslobj", None), "get_verified_chain", None)
    if not callable(get_chain):
        raise ssl.SSLError("OTLP HTTPS requires a runtime exposing its verified TLS chain")
    chain = get_chain()
    if not isinstance(chain, list) or not chain:
        raise ssl.SSLError("OTLP HTTPS requires a nonempty verified TLS chain")
    if not all(_key_is_strong(_certificate_from_verified_item(item)) for item in chain):
        raise ssl.SSLError("OTLP HTTPS certificate key is below the supported security minimum")


def _reject_key_password() -> str:
    raise ssl.SSLError("OTLP HTTPS requires an unencrypted client private key")


def _client_tls_context(cert_file: str, key_file: str | None) -> ssl.SSLContext:
    # Capture once, validate, then load precisely those bytes. Never reopen the
    # operator's paths between validation and SSLContext.load_cert_chain.
    try:
        certificate_bytes = Path(cert_file).read_bytes()
        key_bytes = Path(key_file).read_bytes() if key_file and key_file != cert_file else None
        certificates = x509.load_pem_x509_certificates(certificate_bytes)
        if not certificates or not all(_key_is_strong(cert) for cert in certificates):
            raise ssl.SSLError("Client certificate key is below the security minimum")
        context = _tls_context()
        with tempfile.TemporaryDirectory(prefix="otlp-client-tls-") as directory:
            # NamedTemporaryFile creates each file with owner-only permissions.
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as certificate:
                certificate.write(certificate_bytes)
                certificate_path = certificate.name
            key_path = None
            if key_bytes is not None:
                with tempfile.NamedTemporaryFile(dir=directory, delete=False) as key:
                    key.write(key_bytes)
                    key_path = key.name
            context.load_cert_chain(certificate_path, key_path, password=_reject_key_password)
        return context
    except (OSError, ValueError):
        raise ssl.SSLError("OTLP HTTPS client certificate configuration is invalid") from None


class _VerifiedConnection(HTTPSConnection):
    def connect(self) -> None:
        certificate, key, context = self.cert_file, self.key_file, self.ssl_context
        try:
            if certificate:
                self.ssl_context = _client_tls_context(certificate, key)
                # urllib3 must use the already loaded pair, not the original paths.
                self.cert_file = self.key_file = None
            else:
                # Loading a CA bundle appends roots; never share a mutable context
                # across separate CA selections or new connections after rotation.
                self.ssl_context = _tls_context()
            super().connect()
            _verify_key_lengths(self.sock)
        except Exception:
            self.close()
            raise
        finally:
            self.cert_file, self.key_file, self.ssl_context = certificate, key, context

    def _connect_tls_proxy(self, hostname: str, sock: socket.socket) -> ssl.SSLSocket:
        # Match Requests' selected CA roots without preloading unrelated system roots.
        context = _tls_context()
        if self.ca_certs or self.ca_cert_dir or self.ca_cert_data:
            context.load_verify_locations(self.ca_certs, self.ca_cert_dir, self.ca_cert_data)
        else:
            context.load_default_certs()
        if self.proxy_config is None:
            raise ssl.SSLError("OTLP HTTPS proxy configuration is missing")
        self.proxy_config = self.proxy_config._replace(ssl_context=context)
        connection = super()._connect_tls_proxy(hostname, sock)
        try:
            _verify_key_lengths(connection)
        except Exception:
            connection.close()
            raise
        return connection


class _VerifiedPool(HTTPSConnectionPool):
    ConnectionCls = _VerifiedConnection


class _TLSAdapter(HTTPAdapter):
    def init_poolmanager(
        self, connections: int, maxsize: int, block: bool = False, **pool_kwargs: Any
    ) -> None:
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            **self.poolmanager.pool_classes_by_scheme,
            "https": _VerifiedPool,
        }

    def proxy_manager_for(self, proxy: str, **proxy_kwargs: Any) -> Any:
        if urlsplit(proxy).scheme not in ("http", "https"):
            raise ValueError("OTLP HTTPS supports HTTP and HTTPS CONNECT proxies only")
        manager = super().proxy_manager_for(proxy, **proxy_kwargs)
        manager.pool_classes_by_scheme = {
            **manager.pool_classes_by_scheme,
            "https": _VerifiedPool,
        }
        return manager

    def build_connection_pool_key_attributes(
        self,
        request: requests.PreparedRequest,
        verify: bool | str,
        cert: str | tuple[str, str] | None = None,
    ) -> tuple["_HostParams", "_PoolKwargs"]:
        if verify is False:
            raise ValueError("OTLP HTTPS certificate verification cannot be disabled")
        return super().build_connection_pool_key_attributes(request, verify, cert)


class _HTTPSOnlySession(requests.Session):
    def __init__(self) -> None:
        super().__init__()
        self.mount("https://", _TLSAdapter())

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        if urlsplit(request.url or "").scheme != "https":
            raise ValueError("OTLP http/protobuf requires an HTTPS endpoint")
        kwargs["allow_redirects"] = False
        try:
            response = super().send(request, **kwargs)
        except requests.RequestException as error:
            # The SDK logs exporter exceptions; Requests errors may embed query credentials.
            raise type(error)(
                "OTLP HTTPS transport failed; check TLS policy and connectivity"
            ) from None
        if 300 <= response.status_code < 400:
            response.close()
            raise requests.RequestException(
                "OTLP redirects are disabled; configure the final HTTPS OTLP endpoint"
            )
        return response


def create_exporter(endpoint: str, insecure: bool = True, timeout: float = 10) -> "MetricExporter":
    """Keep component endpoint/timeout while explicitly selecting the wire protocol."""
    protocol = (
        (os.getenv("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL") or "").strip()
        or (os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL") or "").strip()
        or "grpc"
    )
    if protocol == "grpc":
        if urlsplit(endpoint).scheme == "https" or not insecure:
            raise ValueError(
                "TLS gRPC cannot enforce certificate-key minima; configure "
                "OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf and an HTTPS OTLP/HTTP "
                "endpoint (see docs/otlp-transport.md)"
            )
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

        return OTLPMetricExporter(endpoint=endpoint, insecure=insecure, timeout=timeout)
    if protocol != "http/protobuf":
        raise ValueError("OTLP protocol must be grpc or http/protobuf")
    specific_endpoint = os.getenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT")
    selected = specific_endpoint or endpoint
    parsed = urlsplit(selected)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.fragment
    ):
        raise ValueError(
            "OTLP http/protobuf requires an HTTPS endpoint without userinfo or fragment"
        )
    if not specific_endpoint:
        selected = urlunsplit(parsed._replace(path=parsed.path.rstrip("/") + "/v1/metrics"))

    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter as HTTPExporter,
    )

    session = _HTTPSOnlySession()
    try:
        return HTTPExporter(endpoint=selected, timeout=timeout, session=session)
    except Exception:
        session.close()
        raise
