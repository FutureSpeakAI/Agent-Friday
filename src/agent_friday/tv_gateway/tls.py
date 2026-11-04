"""The TV gateway's TLS identity: one self-signed P-256 certificate, made once.

The TV pins this certificate's SPKI SHA-256 at pairing and refuses any other
afterwards, so no certificate authority is involved and nothing is installed
on either side. ``services/local_ca`` is deliberately not used: its name
constraint excludes IP addresses, and the TV does not need a CA.
"""
from __future__ import annotations

import datetime
import hashlib
import ssl
from pathlib import Path


def _dir() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "security" / "tv_gateway"


def ensure_identity() -> tuple:
    """Return (cert_path, key_path), creating them the first time."""
    d = _dir()
    cert_p, key_p = d / "cert.pem", d / "key.pem"
    if cert_p.exists() and key_p.exists():
        return cert_p, key_p
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Agent Friday TV gateway")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    d.mkdir(parents=True, exist_ok=True)
    key_p.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                        serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))
    try:
        import os
        os.chmod(key_p, 0o600)
    except Exception:
        pass
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_p, key_p


def spki_sha256() -> str:
    """Hex SHA-256 of the certificate's SubjectPublicKeyInfo: what the TV pins."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    cert_p, _ = ensure_identity()
    cert = x509.load_pem_x509_certificate(cert_p.read_bytes())
    spki = cert.public_key().public_bytes(serialization.Encoding.DER,
                                          serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(spki).hexdigest()


def server_context() -> ssl.SSLContext:
    cert_p, key_p = ensure_identity()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(str(cert_p), str(key_p))
    return ctx
