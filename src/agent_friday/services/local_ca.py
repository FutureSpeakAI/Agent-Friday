"""Friday's own small certificate authority, for https://agent.<name> on this PC.

A browser shows a padlock for https://agent.<name> only when the certificate
Friday presents chains to an authority Windows trusts. Friday makes that
authority itself, on this machine -- the same idea as mkcert -- with the two
properties the one security decision in all this turns on:

* It can vouch for ONE name. The authority's own certificate carries a critical
  RFC 5280 name constraint permitting only `agent.<name>` and excluding every IP
  address, so even a copy of its key taken off this PC could not mint a
  certificate a browser would accept for any other website. mkcert's authority
  can sign for anything; this one cannot.
* Nothing in this module installs anything. Files are written to Friday's own
  folder and that is all. Trust is added only by
  services/local_address.start_trust_job(), reachable only from the Settings
  button, which runs certutil so that WINDOWS asks the user -- and which refuses
  to run at all under a test.

The leaf certificate has an empty subject and a critical subjectAltName. That is
deliberate: its only name is then the one the constraint permits, so no chain
engine can object to a name form the authority says nothing about.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import ipaddress
import json
import secrets
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_CERT = "ca.pem"
CA_KEY = "ca-key.pem"
LEAF_CERT = "cert.pem"      # the leaf, then the authority: what a TLS server presents
LEAF_KEY = "key.pem"
STATE = "state.json"

CA_YEARS = 10
LEAF_DAYS = 397             # the public-web ceiling, although nothing forces it here
RENEW_WITHIN_DAYS = 30

CA_NAME_PREFIX = "Agent Friday local address CA"


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _pem(cert) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _key_pem(key) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _write(p: Path, data: bytes) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(p)


def thumbprint(cert, algo: str = "sha1") -> str:
    """AB:CD:... as Windows' certificate dialog prints it (it shows SHA-1)."""
    h = hashlib.new(algo, cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2)).upper()


def covers(ca_cert, host: str) -> bool:
    """True when this authority's name constraint permits exactly `host`."""
    try:
        nc = ca_cert.extensions.get_extension_for_class(x509.NameConstraints).value
    except x509.ExtensionNotFound:
        return False
    permitted = [g.value.lower() for g in (nc.permitted_subtrees or [])
                 if isinstance(g, x509.DNSName)]
    return permitted == [host.lower()]


def leaf_names(cert) -> list:
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return []
    return [n.lower() for n in san.get_values_for_type(x509.DNSName)]


def make_ca(host: str):
    """A new authority that may only vouch for `host`. Returns (key, cert)."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([
        # The short tag tells two installs (or an old and a new authority)
        # apart in Windows' certificate list.
        x509.NameAttribute(NameOID.COMMON_NAME,
                           f"{CA_NAME_PREFIX} {secrets.token_hex(2).upper()}"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Agent Friday (this PC only)"),
    ])
    now = _now()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(hours=1))
        .not_valid_after(now + _dt.timedelta(days=365 * CA_YEARS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=False, content_commitment=False,
            key_encipherment=False, data_encipherment=False, key_agreement=False,
            key_cert_sign=True, crl_sign=True, encipher_only=False,
            decipher_only=False), critical=True)
        .add_extension(x509.NameConstraints(
            permitted_subtrees=[x509.DNSName(host)],
            excluded_subtrees=[x509.IPAddress(ipaddress.ip_network("0.0.0.0/0")),
                               x509.IPAddress(ipaddress.ip_network("::/0"))]),
            critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                       critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def make_leaf(host: str, ca_key, ca_cert, days: int = LEAF_DAYS):
    """The certificate the local address presents, signed by the authority."""
    key = ec.generate_private_key(ec.SECP256R1())
    now = _now()
    not_after = min(now + _dt.timedelta(days=days),
                    ca_cert.not_valid_after_utc - _dt.timedelta(days=1))
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(hours=1))
        .not_valid_after(not_after)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=True)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False,
            key_encipherment=False, data_encipherment=False, key_agreement=False,
            key_cert_sign=False, crl_sign=False, encipher_only=False,
            decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                       critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                       critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
            ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def _read_state(d: Path) -> dict:
    try:
        return json.loads((d / STATE).read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def ensure(d: Path, host: str) -> dict:
    """Make sure `d` holds an authority for `host` and a current certificate.

    Creates what is missing, replaces an authority made for a different name
    (the agent was renamed), and renews the certificate within
    RENEW_WITHIN_DAYS of its end. Renewing never needs the user: the authority
    stays the same, so what Windows trusts stays the same. A NEW authority does
    need them again, and `info()["ca"]["sha1"]` changes to say so.
    """
    d.mkdir(parents=True, exist_ok=True)
    state = _read_state(d)
    ca_key = ca_cert = None
    try:
        ca_cert = x509.load_pem_x509_certificate((d / CA_CERT).read_bytes())
        ca_key = serialization.load_pem_private_key((d / CA_KEY).read_bytes(), None)
    except Exception:
        ca_cert = ca_key = None
    now = _now()
    fresh_ca = (ca_cert is None or not covers(ca_cert, host)
                or ca_cert.not_valid_after_utc - now < _dt.timedelta(days=365))
    if fresh_ca:
        if ca_cert is not None:
            # Kept so "Stop trusting" can still find and remove the old one.
            history = list(state.get("previous_cas") or [])
            history.append({"sha1": thumbprint(ca_cert), "subject": ca_cert.subject.rfc4514_string()})
            state["previous_cas"] = history[-10:]
        ca_key, ca_cert = make_ca(host)
        _write(d / CA_KEY, _key_pem(ca_key))
        _write(d / CA_CERT, _pem(ca_cert))
    leaf = None
    try:
        leaf = x509.load_pem_x509_certificate((d / LEAF_CERT).read_bytes())
    except Exception:
        leaf = None
    stale_leaf = (
        fresh_ca or leaf is None or not (d / LEAF_KEY).exists()
        or leaf_names(leaf) != [host.lower()]
        or leaf.issuer != ca_cert.subject
        or leaf.not_valid_after_utc - now < _dt.timedelta(days=RENEW_WITHIN_DAYS)
    )
    if stale_leaf:
        leaf_key, leaf = make_leaf(host, ca_key, ca_cert)
        _write(d / LEAF_KEY, _key_pem(leaf_key))
        _write(d / LEAF_CERT, _pem(leaf) + _pem(ca_cert))
    state.update({"host": host, "ca_sha1": thumbprint(ca_cert)})
    _write(d / STATE, json.dumps(state, indent=2).encode("utf-8"))
    return info(d)


def info(d: Path) -> dict:
    """What is on disk, for the Settings card. Never raises."""
    out = {"exists": False}
    try:
        ca = x509.load_pem_x509_certificate((d / CA_CERT).read_bytes())
    except Exception:
        return out
    out.update({
        "exists": True,
        "subject": ca.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value,
        "sha1": thumbprint(ca, "sha1"),
        "sha256": thumbprint(ca, "sha256"),
        "not_after": ca.not_valid_after_utc.date().isoformat(),
        "path": str(d / CA_CERT),
        "previous": [p.get("sha1") for p in (_read_state(d).get("previous_cas") or [])],
    })
    try:
        nc = ca.extensions.get_extension_for_class(x509.NameConstraints).value
        out["only_for"] = [g.value for g in (nc.permitted_subtrees or [])
                           if isinstance(g, x509.DNSName)]
    except Exception:
        out["only_for"] = []
    try:
        leaf = x509.load_pem_x509_certificate((d / LEAF_CERT).read_bytes())
        out["certificate"] = {"names": leaf_names(leaf),
                              "not_after": leaf.not_valid_after_utc.date().isoformat()}
    except Exception:
        out["certificate"] = None
    return out


def windows_root_thumbprints() -> set:
    """SHA-1 thumbprints (AB:CD:.. form) of every root this user's Windows trusts.

    `ssl.enum_certificates("ROOT")` reads the current user's view of the ROOT
    store, which includes the machine-wide roots -- the same set Edge and
    Chrome consult for a root a person added themselves. Read-only.
    """
    if sys.platform != "win32":
        return set()
    import ssl
    out = set()
    try:
        for der, enc, _trust in ssl.enum_certificates("ROOT"):
            if enc != "x509_asn":
                continue
            h = hashlib.sha1(der, usedforsecurity=False).hexdigest().upper()
            out.add(":".join(h[i:i + 2] for i in range(0, len(h), 2)))
    except Exception:
        pass
    return out
