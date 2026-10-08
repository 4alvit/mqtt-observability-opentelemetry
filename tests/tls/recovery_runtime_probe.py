"""Check the built recovery image's runtime; this does not exercise Kubernetes."""

import json
import os
import platform
import ssl
import sys

import cryptography
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa


def main() -> None:
    assert os.getuid() == os.getgid() == 65532
    assert sys.version_info[:2] == (3, 14)
    assert cryptography.__version__ == "50.0.2"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    assert hasattr(ssl.SSLSocket, "get_verified_chain")
    observed = []
    for bits in (2047, 2048):
        key = rsa.generate_private_key(65537, bits)
        der = key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        decoded = serialization.load_der_public_key(der)
        assert isinstance(decoded, rsa.RSAPublicKey)
        actual = decoded.public_numbers().n.bit_length()
        assert actual == bits
        observed.append(actual)
    for curve in (ec.SECP192R1(), ec.SECP224R1(), ec.SECP256R1()):
        key = ec.generate_private_key(curve)
        der = key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        decoded = serialization.load_der_public_key(der)
        assert isinstance(decoded, ec.EllipticCurvePublicKey)
        assert decoded.key_size == curve.key_size
        observed.append(decoded.key_size)
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "machine": platform.machine(),
                "uid": os.getuid(),
                "gid": os.getgid(),
                "cryptography": cryptography.__version__,
                "openssl": ssl.OPENSSL_VERSION,
                "decoded_key_bits": observed,
                "kubernetes_transport_tested": False,
            }
        )
    )


if __name__ == "__main__":
    main()
