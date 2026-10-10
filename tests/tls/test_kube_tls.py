"""Synthetic loopback PKI for the actual Kubernetes recovery client."""

# Pytest injects named fixtures; native peer fixtures retain complete protocol state.
# pylint: disable=redefined-outer-name,too-many-locals,too-many-statements,too-many-arguments

# SPDX-License-Identifier: MIT
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
# Adapted from MIT-licensed energy-data-rag-pipeline TLS tests.

import http.client
import os
import select
import socket
import ssl
import threading
import urllib.error
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from recovery import backup

Key = rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey
CHAIN_CASES = (
    "strong",
    "strong-ec",
    "weak-leaf",
    "weak-intermediate",
    "weak-root",
    "weak-2047-root",
    "weak-ec-root",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep host proxy and trust settings outside the synthetic fixture."""
    for name in os.environ:
        if name.upper().endswith("_PROXY") or name in (
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE",
        ):
            monkeypatch.delenv(name)


def certificate(
    key: Key, name: str, issuer: x509.Certificate | None, issuer_key: Key, *, ca: bool
) -> x509.Certificate:
    """Issue an ephemeral certificate with independently checkable key strength."""
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer.subject if issuer else subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=not ca,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=ca,
                crl_sign=ca,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
            False,
        )
    )
    if not ca:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.DNSName("sub.localhost")]
            ),
            False,
        ).add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            False,
        )
    return builder.sign(issuer_key, hashes.SHA256())


@pytest.fixture(scope="module")
def chains(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, tuple[Path, Path, Path]]:
    """Prepare strong and weak leaf, intermediate and root certificate chains."""
    directory = tmp_path_factory.mktemp("otlp-synthetic-pki")
    result = {}
    for case in CHAIN_CASES:
        root_bits = {"weak-root": 1024, "weak-2047-root": 2047}.get(case, 2048)
        root_key: Key
        if case in ("strong-ec", "weak-ec-root"):
            curve = ec.SECP192R1() if case == "weak-ec-root" else ec.SECP256R1()
            root_key = ec.generate_private_key(curve)
        else:
            root_key = rsa.generate_private_key(65537, root_bits)
        root = certificate(root_key, case, None, root_key, ca=True)
        issuer, issuer_key = (root, root_key)
        intermediate = b""
        if case == "weak-intermediate":
            issuer_key = rsa.generate_private_key(65537, 1024)
            issuer = certificate(issuer_key, "intermediate", root, root_key, ca=True)
            intermediate = issuer.public_bytes(serialization.Encoding.PEM)
        leaf_key: Key = (
            ec.generate_private_key(ec.SECP256R1())
            if case == "strong-ec"
            else rsa.generate_private_key(65537, 1024 if case == "weak-leaf" else 2048)
        )
        leaf = certificate(leaf_key, "localhost", issuer, issuer_key, ca=False)
        cert_file, key_file, ca_file = (
            directory / f"{case}.{suffix}" for suffix in ("pem", "key", "ca")
        )
        cert_file.write_bytes(
            leaf.public_bytes(serialization.Encoding.PEM) + intermediate
        )
        key_file.write_bytes(
            leaf_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        ca_file.write_bytes(root.public_bytes(serialization.Encoding.PEM))
        result[case] = (cert_file, key_file, ca_file)
    return result


@contextmanager
def peer(
    chain: tuple[Path, Path, Path],
    version: ssl.TLSVersion,
    *,
    redirect: str | None = None,
    client_auth: bool = False,
    response_body: bytes = b"",
    redirect_code: int = 307,
) -> Iterator[tuple[int, dict[str, Any]]]:
    """Observe whether the client sends application bytes to a native TLS peer."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = version
    context.set_ciphers("DEFAULT:@SECLEVEL=0")
    context.load_cert_chain(chain[0], chain[1])
    context.set_alpn_protocols(["h2", "http/1.1"])
    if client_auth:
        context.load_verify_locations(chain[2])
        context.verify_mode = ssl.CERT_REQUIRED
    result: dict[str, Any] = {"application_bytes": b""}
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)

        def worker() -> None:
            try:
                raw, _ = listener.accept()
                with raw:
                    raw.settimeout(5)
                    with context.wrap_socket(raw, server_side=True) as connection:
                        result["tls"] = connection.version()
                        result["alpn"] = connection.selected_alpn_protocol()
                        if client_auth:
                            result["client_certificate"] = connection.getpeercert(
                                binary_form=True
                            )
                        data = b""
                        while b"\r\n\r\n" not in data:
                            part = connection.recv(8192)
                            if not part:
                                if not data:
                                    result["closed_before_http"] = True
                                    return
                                raise EOFError("Expected HTTP headers")
                            data += part
                            result["application_bytes"] = data
                        headers, body = data.split(b"\r\n\r\n", 1)
                        length = next(
                            (
                                int(line.split(b":", 1)[1])
                                for line in headers.split(b"\r\n")
                                if line.lower().startswith(b"content-length:")
                            ),
                            0,
                        )
                        while len(body) < length:
                            part = connection.recv(8192)
                            if not part:
                                raise EOFError("Expected complete OTLP body")
                            body += part
                        result["application_bytes"] = headers + b"\r\n\r\n" + body
                        response = (
                            f"HTTP/1.1 {redirect_code} Redirect\r\nLocation: {redirect}\r\n"
                            if redirect
                            else "HTTP/1.1 200 OK\r\n"
                        )
                        connection.sendall(
                            (
                                response
                                + f"Content-Length: {len(response_body)}\r\n"
                                + "Content-Type: application/json\r\nConnection: close\r\n\r\n"
                            ).encode()
                            + response_body
                        )
            except ssl.SSLError as error:
                result["handshake_error"] = str(error)
            except (ConnectionResetError, BrokenPipeError) as error:
                if result["application_bytes"]:
                    result["unexpected_error"] = repr(error)
                else:
                    result["closed_before_http"] = True
            # pylint: disable-next=broad-exception-caught
            except Exception as error:  # noqa: BLE001
                # Propagate every worker failure to assertions on the parent thread.
                result["unexpected_error"] = repr(error)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            yield (listener.getsockname()[1], result)
        finally:
            thread.join(6)
            assert not thread.is_alive()
            assert "unexpected_error" not in result, result


def calibrate(chain: tuple[Path, Path, Path], version: ssl.TLSVersion) -> None:
    """Prove the fixture serves a trusted handshake with a relaxed key policy."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = version
    context.set_ciphers("DEFAULT:@SECLEVEL=0")
    context.load_verify_locations(chain[2])
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    with peer(chain, version) as (port, observed):
        connection = http.client.HTTPSConnection(
            "localhost", port, context=context, timeout=5
        )
        try:
            connection.request("POST", "/oracle", body=b"calibration")
            assert connection.getresponse().status == 200
        finally:
            connection.close()
    assert observed["application_bytes"].endswith(b"calibration")
    assert observed["tls"] == version.name.replace("_", ".")


@contextmanager
def proxy(
    target_port: int, chain: tuple[Path, Path, Path] | None
) -> Iterator[tuple[int, list[bytes]]]:
    """A bounded CONNECT relay whose only permitted destination is our loopback peer."""
    observed: list[bytes] = []
    errors: list[Exception] = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)

        def worker() -> None:
            try:
                raw, _ = listener.accept()
                with raw:
                    raw.settimeout(5)
                    if chain:
                        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                        context.minimum_version = ssl.TLSVersion.TLSv1_2
                        context.set_ciphers("DEFAULT:@SECLEVEL=0")
                        context.load_cert_chain(chain[0], chain[1])
                        incoming: socket.socket = context.wrap_socket(
                            raw, server_side=True
                        )
                    else:
                        incoming = raw
                    with incoming:
                        request = b""
                        while b"\r\n\r\n" not in request:
                            data = incoming.recv(8192)
                            if not data:
                                return
                            request += data
                        observed.append(request)
                        assert request.startswith(
                            f"CONNECT localhost:{target_port} ".encode()
                        )
                        with socket.create_connection(
                            ("127.0.0.1", target_port), timeout=5
                        ) as out:
                            incoming.sendall(
                                b"HTTP/1.1 200 Connection established\r\n\r\n"
                            )
                            while True:
                                ready, _, _ = select.select([incoming, out], [], [], 5)
                                if not ready:
                                    raise TimeoutError("Synthetic proxy did not finish")
                                for source in ready:
                                    data = source.recv(8192)
                                    if not data:
                                        return
                                    destination = (
                                        out if source is incoming else incoming
                                    )
                                    destination.sendall(data)
            except ssl.SSLError:
                pass
            except (ConnectionResetError, BrokenPipeError) as error:
                if observed:
                    errors.append(error)
            # pylint: disable-next=broad-exception-caught
            except Exception as error:  # noqa: BLE001
                # Propagate every worker failure to assertions on the parent thread.
                errors.append(error)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            yield (listener.getsockname()[1], observed)
        finally:
            thread.join(6)
            assert not thread.is_alive()
            assert not errors, errors


def actual_client(tmp_path, monkeypatch, ca, host, port):
    """Construct the production client with a temporary service-account fixture."""
    account = tmp_path / "service-account"
    account.mkdir(exist_ok=True)
    (account / "token").write_text("synthetic-kube-token\n")
    (account / "ca.crt").write_bytes(ca.read_bytes())
    real_path = Path
    monkeypatch.setattr(
        backup,
        "Path",
        lambda p: (
            account
            if p == "/var/run/secrets/kubernetes.io/serviceaccount"
            else real_path(p)
        ),
    )
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", host)
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT_HTTPS", str(port))
    return backup.Kube()


@pytest.mark.parametrize("version", [ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3])
@pytest.mark.parametrize("case", [*CHAIN_CASES, "untrusted", "wrong-host"])
def test_actual_kube_get_checks_chain_before_bearer(
    tmp_path, monkeypatch, chains, version, case
):
    """Block invalid chains before the actual bearer GET is transmitted."""
    chain = chains.get(case, chains["strong"])
    calibrate(chain, version)
    ca = chains["strong-ec"][2] if case == "untrusted" else chain[2]
    with peer(chain, version, response_body=b'{"kind":"PodList"}') as (port, observed):
        host = "127.0.0.1" if case == "wrong-host" else "localhost"
        client = actual_client(tmp_path, monkeypatch, ca, host, port)
        if case.startswith("strong"):
            assert client.get("/api/v1/pods") == {"kind": "PodList"}
        else:
            with pytest.raises(urllib.error.URLError):
                client.get("/api/v1/pods")
    if case.startswith("strong"):
        assert (
            b"Authorization: Bearer synthetic-kube-token"
            in observed["application_bytes"]
        )
        assert observed["tls"] == version.name.replace("_", ".")
        assert observed["alpn"] == "http/1.1"
    else:
        assert observed["application_bytes"] == b""


@pytest.mark.parametrize("redirect_code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("scheme", ["http", "https"])
def test_api_redirect_does_not_forward_token(
    tmp_path, monkeypatch, chains, redirect_code, scheme
):
    """Reject every redirect type before contacting its HTTP or HTTPS target."""
    with socket.socket() as target:
        target.bind(("127.0.0.1", 0))
        target.listen(1)
        target.settimeout(0.1)
        redirect = f"{scheme}://127.0.0.1:{target.getsockname()[1]}/capture"
        with peer(
            chains["strong"],
            ssl.TLSVersion.TLSv1_3,
            redirect=redirect,
            redirect_code=redirect_code,
        ) as (
            port,
            _,
        ):
            client = actual_client(
                tmp_path, monkeypatch, chains["strong"][2], "localhost", port
            )
            with pytest.raises(urllib.error.HTTPError, match="redirects are disabled"):
                client.get("/api/v1/pods")
        with pytest.raises(TimeoutError):
            target.accept()


@pytest.mark.parametrize("scheme", ["https", "ftp", "socks5"])
def test_unsupported_proxy_rejected_before_connect_or_credentials(
    tmp_path, monkeypatch, chains, scheme
):
    """Reject unsupported proxy transports before connecting or sending credentials."""
    with socket.socket() as target:
        target.bind(("127.0.0.1", 0))
        target.listen(1)
        target.settimeout(0.1)
        monkeypatch.setenv(
            "HTTPS_PROXY",
            f"{scheme}://user:synthetic@localhost:{target.getsockname()[1]}",
        )
        client = actual_client(
            tmp_path, monkeypatch, chains["strong"][2], "localhost", 1
        )
        with pytest.raises(ssl.SSLError, match="supports only HTTP-scheme proxies"):
            client.get("/api/v1/pods")
        with pytest.raises(TimeoutError):
            target.accept()


def test_no_proxy_preserves_direct_kube_request(tmp_path, monkeypatch, chains):
    """Allow direct verified access when the configured proxy is bypassed."""
    monkeypatch.setenv("HTTPS_PROXY", "https://user:synthetic@localhost:1")
    monkeypatch.setenv("NO_PROXY", "localhost")
    with peer(chains["strong"], ssl.TLSVersion.TLSv1_3, response_body=b"{}") as (
        port,
        observed,
    ):
        client = actual_client(
            tmp_path, monkeypatch, chains["strong"][2], "localhost", port
        )
        assert client.get("/api/v1/pods") == {}
    assert (
        b"Authorization: Bearer synthetic-kube-token" in observed["application_bytes"]
    )


@pytest.mark.parametrize("case", ["strong", "weak-2047-root"])
def test_http_proxy_preserves_origin_chain_policy(tmp_path, monkeypatch, chains, case):
    """Apply the same origin policy inside an ordinary HTTP CONNECT tunnel."""
    chain = chains[case]
    calibrate(chain, ssl.TLSVersion.TLSv1_3)
    # Split dependent contexts so pylint can see where the tunnel port is bound.
    with peer(chain, ssl.TLSVersion.TLSv1_3, response_body=b"{}") as peer_result:
        port, received = peer_result
        with proxy(port, None) as (proxy_port, observed):
            monkeypatch.setenv("HTTPS_PROXY", f"http://localhost:{proxy_port}")
            client = actual_client(tmp_path, monkeypatch, chain[2], "localhost", port)
            if case == "strong":
                assert client.get("/api/v1/pods") == {}
            else:
                with pytest.raises(urllib.error.URLError, match="security minimum"):
                    client.get("/api/v1/pods")
    assert len(observed) == 1
    if case == "strong":
        assert (
            b"Authorization: Bearer synthetic-kube-token"
            in received["application_bytes"]
        )
    else:
        assert received["application_bytes"] == b""
