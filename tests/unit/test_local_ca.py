"""Friday's certificate authority can vouch for agent.<name> and nothing else.

That is the security property a person is asked to accept when Windows shows
its warning, so it is checked two ways here, neither of which installs anything:

  * OpenSSL (a real TLS handshake against a throwaway server) and
  * Windows' own chain engine (CertGetCertificateChain), with the authority in
    a PRIVATE in-memory root store (hExclusiveRoot) -- the same engine Edge and
    Chrome defer to for a root a person added on Windows.

Both must accept the genuine certificate and refuse one the same authority
signed for another website or for an IP address.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import socket
import ssl
import sys
import threading

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from agent_friday.services import local_ca

HOST = "agent.pwtest"


def _load(d):
    ca = x509.load_pem_x509_certificate((d / local_ca.CA_CERT).read_bytes())
    key = serialization.load_pem_private_key((d / local_ca.CA_KEY).read_bytes(), None)
    leaf = x509.load_pem_x509_certificate((d / local_ca.LEAF_CERT).read_bytes())
    return ca, key, leaf


def _ip_leaf(ca, ca_key, ip="93.184.216.34"):
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(x509.Name([])).issuer_name(ca.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(hours=1)).not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip))]), critical=True)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    return key, cert


def test_the_authority_is_locked_to_one_name(tmp_path):
    info = local_ca.ensure(tmp_path, HOST)
    ca, _key, leaf = _load(tmp_path)
    nc = ca.extensions.get_extension_for_class(x509.NameConstraints)
    assert nc.critical
    assert [g.value for g in nc.value.permitted_subtrees] == [HOST]
    excluded = {str(g.value) for g in nc.value.excluded_subtrees}
    assert excluded == {"0.0.0.0/0", "::/0"}
    bc = ca.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert bc.ca and bc.path_length == 0
    assert info["only_for"] == [HOST]
    assert info["subject"].startswith(local_ca.CA_NAME_PREFIX)
    # the leaf: its only name is the permitted one
    assert leaf.subject == x509.Name([])
    assert local_ca.leaf_names(leaf) == [HOST]
    assert leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).critical


def test_renaming_the_agent_makes_a_new_authority_and_remembers_the_old(tmp_path):
    first = local_ca.ensure(tmp_path, "agent.one")
    again = local_ca.ensure(tmp_path, "agent.one")
    assert again["sha1"] == first["sha1"]             # stable: nothing to re-trust
    second = local_ca.ensure(tmp_path, "agent.two")
    assert second["sha1"] != first["sha1"]
    assert second["only_for"] == ["agent.two"]
    assert first["sha1"] in second["previous"]         # so "Stop trusting" can find it


def test_a_certificate_near_its_end_is_renewed_under_the_same_authority(tmp_path):
    first = local_ca.ensure(tmp_path, HOST)
    ca, ca_key, _leaf = _load(tmp_path)
    key, short = local_ca.make_leaf(HOST, ca_key, ca, days=5)
    (tmp_path / local_ca.LEAF_CERT).write_bytes(
        short.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM))
    (tmp_path / local_ca.LEAF_KEY).write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    renewed = local_ca.ensure(tmp_path, HOST)
    assert renewed["sha1"] == first["sha1"]
    assert renewed["certificate"]["not_after"] > (dt.date.today() + dt.timedelta(days=300)).isoformat()


def _serve_once(cert_pem: bytes, key_pem: bytes, tmp_path):
    """A TLS server on an ephemeral loopback port that completes one handshake."""
    (tmp_path / "srv.pem").write_bytes(cert_pem)
    (tmp_path / "srv.key").write_bytes(key_pem)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(tmp_path / "srv.pem"), str(tmp_path / "srv.key"))
    lsock = socket.socket()
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(1)

    def run():
        try:
            conn, _ = lsock.accept()
            with ctx.wrap_socket(conn, server_side=True) as s:
                s.recv(1)
        except Exception:
            pass
        finally:
            lsock.close()

    threading.Thread(target=run, daemon=True).start()
    return lsock.getsockname()[1]


def _handshake(port, ca_file, server_name):
    # With a cafile, create_default_context trusts ONLY that file, not the system.
    ctx = ssl.create_default_context(cafile=str(ca_file))
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with ctx.wrap_socket(raw, server_hostname=server_name) as s:
            s.send(b"x")


def _pem(c):
    return c.public_bytes(serialization.Encoding.PEM)


def _key(k):
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption())


def test_openssl_accepts_the_genuine_certificate(tmp_path):
    local_ca.ensure(tmp_path, HOST)
    port = _serve_once((tmp_path / local_ca.LEAF_CERT).read_bytes(),
                       (tmp_path / local_ca.LEAF_KEY).read_bytes(), tmp_path)
    _handshake(port, tmp_path / local_ca.CA_CERT, HOST)


def test_openssl_refuses_a_certificate_the_authority_signed_for_another_site(tmp_path):
    local_ca.ensure(tmp_path, HOST)
    ca, ca_key, _ = _load(tmp_path)
    key, rogue = local_ca.make_leaf("www.bank.example", ca_key, ca)
    port = _serve_once(_pem(rogue) + _pem(ca), _key(key), tmp_path)
    with pytest.raises(ssl.SSLCertVerificationError, match="(?i)name constraint|permitted"):
        _handshake(port, tmp_path / local_ca.CA_CERT, "www.bank.example")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own chain engine")
class TestWindowsChainEngine:
    """CertGetCertificateChain with the authority as an EXCLUSIVE in-memory root.
    Nothing is added to any certificate store."""

    @staticmethod
    def _check(ca_cert, leaf_cert, server_name):
        import ctypes as C
        import ctypes.wintypes as W

        crypt32 = C.WinDLL("crypt32", use_last_error=True)

        class ENGINE(C.Structure):
            _fields_ = [("cbSize", W.DWORD), ("hRestrictedRoot", C.c_void_p),
                        ("hRestrictedTrust", C.c_void_p), ("hRestrictedOther", C.c_void_p),
                        ("cAdditionalStore", W.DWORD), ("rghAdditionalStore", C.c_void_p),
                        ("dwFlags", W.DWORD), ("dwUrlRetrievalTimeout", W.DWORD),
                        ("MaximumCachedCertificates", W.DWORD), ("CycleDetectionModulus", W.DWORD),
                        ("hExclusiveRoot", C.c_void_p), ("hExclusiveTrustedPeople", C.c_void_p),
                        ("dwExclusiveFlags", W.DWORD)]

        class USAGE(C.Structure):
            _fields_ = [("cUsageIdentifier", W.DWORD), ("rgpszUsageIdentifier", C.POINTER(C.c_char_p))]

        class MATCH(C.Structure):
            _fields_ = [("dwType", W.DWORD), ("Usage", USAGE)]

        class PARA(C.Structure):
            _fields_ = [("cbSize", W.DWORD), ("RequestedUsage", MATCH)]

        class TRUST(C.Structure):
            _fields_ = [("dwErrorStatus", W.DWORD), ("dwInfoStatus", W.DWORD)]

        class CHAIN(C.Structure):
            _fields_ = [("cbSize", W.DWORD), ("TrustStatus", TRUST)]

        class SSLX(C.Structure):
            _fields_ = [("cbSize", W.DWORD), ("dwAuthType", W.DWORD), ("fdwChecks", W.DWORD),
                        ("pwszServerName", W.LPCWSTR)]

        class PPARA(C.Structure):
            _fields_ = [("cbSize", W.DWORD), ("dwFlags", W.DWORD), ("pvExtraPolicyPara", C.c_void_p)]

        class PSTAT(C.Structure):
            _fields_ = [("cbSize", W.DWORD), ("dwError", W.DWORD), ("lChainIndex", C.c_long),
                        ("lElementIndex", C.c_long), ("pvExtraPolicyStatus", C.c_void_p)]

        crypt32.CertOpenStore.restype = C.c_void_p
        crypt32.CertOpenStore.argtypes = [C.c_void_p, W.DWORD, C.c_void_p, W.DWORD, C.c_void_p]
        crypt32.CertCreateCertificateContext.restype = C.c_void_p
        crypt32.CertCreateCertificateContext.argtypes = [W.DWORD, C.c_char_p, W.DWORD]
        crypt32.CertAddCertificateContextToStore.argtypes = [C.c_void_p, C.c_void_p, W.DWORD, C.c_void_p]
        crypt32.CertCreateCertificateChainEngine.argtypes = [C.POINTER(ENGINE), C.POINTER(C.c_void_p)]
        crypt32.CertGetCertificateChain.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p,
                                                    C.POINTER(PARA), W.DWORD, C.c_void_p,
                                                    C.POINTER(C.c_void_p)]
        crypt32.CertVerifyCertificateChainPolicy.argtypes = [C.c_void_p, C.c_void_p,
                                                             C.POINTER(PPARA), C.POINTER(PSTAT)]
        crypt32.CertFreeCertificateChain.argtypes = [C.c_void_p]
        crypt32.CertFreeCertificateChainEngine.argtypes = [C.c_void_p]
        crypt32.CertFreeCertificateContext.argtypes = [C.c_void_p]
        crypt32.CertCloseStore.argtypes = [C.c_void_p, W.DWORD]
        enc = 0x00010001
        der = lambda c: c.public_bytes(serialization.Encoding.DER)  # noqa: E731
        root = crypt32.CertOpenStore(C.c_void_p(2), 0, None, 0, None)     # an in-memory store
        extra = crypt32.CertOpenStore(C.c_void_p(2), 0, None, 0, None)
        b = der(ca_cert)
        ca_ctx = crypt32.CertCreateCertificateContext(enc, b, len(b))
        assert crypt32.CertAddCertificateContextToStore(root, ca_ctx, 4, None)
        lb = der(leaf_cert)
        leaf_ctx = crypt32.CertCreateCertificateContext(enc, lb, len(lb))
        cfg = ENGINE()
        cfg.cbSize = C.sizeof(cfg)
        cfg.hExclusiveRoot = root
        eng = C.c_void_p()
        assert crypt32.CertCreateCertificateChainEngine(C.byref(cfg), C.byref(eng))
        oid = (C.c_char_p * 1)(b"1.3.6.1.5.5.7.3.1")
        para = PARA()
        para.cbSize = C.sizeof(para)
        para.RequestedUsage.Usage.cUsageIdentifier = 1
        para.RequestedUsage.Usage.rgpszUsageIdentifier = oid
        chain = C.c_void_p()
        assert crypt32.CertGetCertificateChain(eng, leaf_ctx, None, extra, C.byref(para), 0, None,
                                               C.byref(chain))
        errors = C.cast(chain, C.POINTER(CHAIN)).contents.TrustStatus.dwErrorStatus
        x = SSLX(C.sizeof(SSLX), 2, 0, server_name)                   # AUTHTYPE_SERVER
        pp = PPARA(C.sizeof(PPARA), 0, C.cast(C.pointer(x), C.c_void_p))
        ps = PSTAT()
        ps.cbSize = C.sizeof(ps)
        crypt32.CertVerifyCertificateChainPolicy(C.c_void_p(4), chain, C.byref(pp), C.byref(ps))
        crypt32.CertFreeCertificateChain(chain)
        crypt32.CertFreeCertificateChainEngine(eng)
        crypt32.CertFreeCertificateContext(leaf_ctx)
        crypt32.CertFreeCertificateContext(ca_ctx)
        crypt32.CertCloseStore(extra, 0)
        crypt32.CertCloseStore(root, 0)
        return errors, ps.dwError

    NOT_PERMITTED = 0x4000   # CERT_TRUST_HAS_NOT_PERMITTED_NAME_CONSTRAINT
    EXCLUDED = 0x8000        # CERT_TRUST_HAS_EXCLUDED_NAME_CONSTRAINT

    def test_windows_accepts_the_genuine_certificate(self, tmp_path):
        local_ca.ensure(tmp_path, HOST)
        ca, _key, leaf = _load(tmp_path)
        errors, policy = self._check(ca, leaf, HOST)
        assert errors == 0 and policy == 0, (hex(errors), hex(policy))

    def test_windows_refuses_another_site(self, tmp_path):
        local_ca.ensure(tmp_path, HOST)
        ca, ca_key, _ = _load(tmp_path)
        _k, rogue = local_ca.make_leaf("www.bank.example", ca_key, ca)
        errors, policy = self._check(ca, rogue, "www.bank.example")
        assert errors & self.NOT_PERMITTED and policy != 0

    def test_windows_refuses_an_ip_address(self, tmp_path):
        local_ca.ensure(tmp_path, HOST)
        ca, ca_key, _ = _load(tmp_path)
        _k, rogue = _ip_leaf(ca, ca_key)
        errors, policy = self._check(ca, rogue, "93.184.216.34")
        assert errors & self.EXCLUDED and policy != 0
