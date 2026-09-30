"""Second-pass coverage for the credential deny-list and the egress classifier.

Routes a hostile prompt takes once the direct ones are closed: the sandboxed
Python tool, a listing piped into a reader, output re-encoded (hex, rot13,
base32, layered or UTF-16 base64, gzip, chunked or lower-cased armor), and the
opposite failure: public X.509 certificates that must stay readable. All
secret values are synthetic.
"""
from __future__ import annotations

import base64
import codecs
import datetime
import gzip
import json

import pytest

from agent_friday.services import credential_paths as cred
from agent_friday.services import secret_patterns as sp
from agent_friday.services import sensitivity_classifier as sc

PEM_HEAD = "-----BEGIN OPENSSH PRIVATE " + "KEY-----"  # pragma: allowlist secret
PEM_TAIL = "-----END OPENSSH PRIVATE " + "KEY-----"  # pragma: allowlist secret
PEM_BODY = ("b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n"
            "QyNTUxOQAAACBSYNTHETICSENTINELKEYMATERIAL0123456789abcdef\n")
PEM = PEM_HEAD + "\n" + PEM_BODY + PEM_TAIL + "\n"
SENTINEL = "SENTINELKEYMATERIAL"


def _write(p, text=PEM):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _tier(text: str) -> int:
    return sc.classify(text, use_presidio=False, use_embeddings=False, egress=True)


# ── real key and certificate encodings ───────────────────────────────────────

def _key_pem(kind: str) -> str:
    from cryptography.hazmat.primitives import serialization as s
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    if kind.startswith("rsa"):
        key = rsa.generate_private_key(public_exponent=65537, key_size=int(kind.split("-")[1]))
    else:
        key = ec.generate_private_key(ec.SECP256R1())
    fmt = (s.PrivateFormat.TraditionalOpenSSL if kind.endswith("pkcs1")
           else s.PrivateFormat.PKCS8)
    return key.private_bytes(s.Encoding.PEM, fmt, s.NoEncryption()).decode()


def _cert_pem(bits: int) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization as s
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "example.test")])
    now = datetime.datetime(2026, 1, 1)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(1)
            .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName(
                [x509.DNSName("a.example.test"), x509.DNSName("b.example.test")]),
                critical=False)
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(s.Encoding.PEM).decode()


def _strip_armor(pem: str) -> str:
    return "\n".join(l for l in pem.splitlines() if not l.startswith("-----"))


KEY_KINDS = ["rsa-2048-pkcs1", "rsa-2048-pkcs8", "rsa-3072-pkcs8", "ec-pkcs8", "ec-pkcs1"]


@pytest.mark.parametrize("kind", KEY_KINDS)
def test_a_real_key_body_without_armor_is_key_material(kind):
    body = _strip_armor(_key_pem(kind))
    assert sp.contains_key_material(body), kind
    assert _tier(body) == sc.Tier.SENSITIVE
    assert sp.WITHHELD in sp.redact(body)
    assert body.splitlines()[1] not in sp.redact("x\n" + body)


@pytest.mark.parametrize("bits", [2048, 4096])
def test_a_real_x509_certificate_is_not_a_private_key(bits, test_home):
    pem = _cert_pem(bits)
    assert not sp.contains_key_material(pem)
    assert not sp.contains_key_material(_strip_armor(pem))
    assert sp.redact(pem) == pem
    assert _tier(pem) != sc.Tier.SENSITIVE
    p = _write(test_home / "Documents" / "site-cert.pem", pem)
    assert cred.check(p) is None
    import agent_friday.services.agent as agent
    assert "BEGIN CERTIFICATE" in agent._tool_read_file({"path": str(p)})


# ── sandboxed python ──────────────────────────────────────────────────────────

def test_sandbox_refuses_code_that_names_a_credential_path(monkeypatch):
    import agent_friday.services.agent as agent
    from agent_friday.services import code_sandbox
    ran = []
    monkeypatch.setattr(code_sandbox, "run", lambda *a, **k: ran.append(1) or {"ok": True})
    for code in ("print(open(r'C:\\Users\\x\\.ssh\\id_rsa').read())",
                 "import pathlib; print(pathlib.Path.home().joinpath('.s'+'sh','id_ed25519').read_text())",
                 "open('/home/x/.aws/credentials').read()"):
        out = agent._tool_run_sandboxed({"code": code})
        assert "won't run" in out.lower(), code
    assert not ran


def test_sandbox_output_that_carries_a_key_is_redacted(monkeypatch):
    import agent_friday.services.agent as agent
    from agent_friday.services import code_sandbox
    monkeypatch.setattr(code_sandbox, "run", lambda *a, **k: {
        "ok": True, "exit_code": 0, "stdout": "hi\n" + PEM, "stderr": ""})
    out = agent._tool_run_sandboxed({"code": "print(open('/srv/thing').read())"})
    assert SENTINEL not in out
    assert "hi" in json.loads(out)["stdout"]


def test_sandbox_ordinary_code_still_runs(monkeypatch):
    import agent_friday.services.agent as agent
    from agent_friday.services import code_sandbox
    monkeypatch.setattr(code_sandbox, "run", lambda *a, **k: {
        "ok": True, "exit_code": 0, "stdout": "4\n", "stderr": ""})
    assert json.loads(agent._tool_run_sandboxed({"code": "print(2+2)"}))["stdout"] == "4\n"


# ── a listing piped into a reader ─────────────────────────────────────────────

def test_a_directory_listing_piped_into_a_reader_is_refused_when_it_holds_a_key(test_home):
    d = test_home / "Documents" / "stash"
    _write(d / "xfile.txt")
    assert cred.scan_command(f"Get-ChildItem '{d}' -Filter x* | Get-Content")


def test_the_same_pipeline_over_an_ordinary_directory_runs(test_home):
    d = test_home / "Documents" / "notes"
    _write(d / "xfile.txt", "milk and eggs")
    assert cred.scan_command(f"Get-ChildItem '{d}' -Filter x* | Get-Content") is None


# ── output re-encoded on the way out ──────────────────────────────────────────

ENCODINGS = {
    "hex dashed (BitConverter)": lambda s: "-".join(f"{b:02X}" for b in s.encode()),
    "hex plain": lambda s: s.encode().hex(),
    "hex 0x list": lambda s: ",".join(f"0x{b:02x}" for b in s.encode()),
    "rot13": lambda s: codecs.encode(s, "rot13"),
    "base32": lambda s: base64.b32encode(s.encode()).decode(),
    "double base64": lambda s: base64.b64encode(base64.b64encode(s.encode())).decode(),
    "utf-16 base64": lambda s: base64.b64encode(s.encode("utf-16-le")).decode(),
    "gzip base64": lambda s: base64.b64encode(gzip.compress(s.encode())).decode(),
    "gzip hex": lambda s: gzip.compress(s.encode()).hex(),
    "lower-cased armor": lambda s: "-----begin rsa private key-----\nQUJDREVGRw==\n",
    "chunked armor": lambda s: '"-----BEGIN RSA PRIV" + "ATE KEY-----"\nQUJDREVGRw==\n',
}


@pytest.mark.parametrize("label", list(ENCODINGS))
def test_reencoded_key_is_key_material(label):
    text = "out:\n" + ENCODINGS[label](PEM)
    assert sp.contains_key_material(text), label
    assert _tier(text) == sc.Tier.SENSITIVE, label


@pytest.mark.parametrize("label", list(ENCODINGS))
def test_reencoded_key_in_command_output_is_withheld(label, monkeypatch):
    import agent_friday.services.agent as agent

    class P:
        stdout = "listing\n" + ENCODINGS[label](PEM) + "\ndone"
        stderr = ""
        returncode = 0
    monkeypatch.setattr(agent.subprocess, "run", lambda *a, **k: P())
    out = agent._tool_run_command({"command": "Get-ChildItem C:\\Temp\\odd"})
    probe = ENCODINGS[label](PEM)
    assert probe.splitlines()[0][:30] not in out, label
    assert not sp.contains_key_material(out), label


def test_encoded_ordinary_text_is_left_alone():
    text = "note: " + base64.b64encode(b"a perfectly ordinary sentence about lunch plans").decode()
    assert not sp.contains_secret(text)
    assert sp.redact(text) == text
    hexed = "digest " + ("ab12" * 16)
    assert sp.redact(hexed) == hexed


# ── classifier false positives ────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "PWD=/c/Users/x/Projects",  # pragma: allowlist secret
    "pwd = ~/notes/today",  # pragma: allowlist secret
    r"Pwd=C:\Users\x\Documents",  # pragma: allowlist secret
    "Bearer authentication-schemes-and-their-uses",
])
def test_ordinary_shell_and_prose_are_not_sensitive(text):
    assert _tier(text) != sc.Tier.SENSITIVE, text


@pytest.mark.parametrize("text", [
    "password=hunter2hunter2",  # pragma: allowlist secret
    "Pwd=S3cret!pass;",  # pragma: allowlist secret
    "Authorization: Bearer abc123def456ghi789jkl012",
])
def test_real_password_and_bearer_forms_are_still_sensitive(text):
    assert _tier(text) == sc.Tier.SENSITIVE, text


# ── search cost ───────────────────────────────────────────────────────────────

def test_name_search_does_not_open_every_file(test_home, monkeypatch):
    from agent_friday.services import file_search
    for i in range(30):
        _write(test_home / "Downloads" / f"plain{i}.txt", "nothing here")
    opened = []
    real = cred._holds_private_key
    monkeypatch.setattr(cred, "_holds_private_key",
                        lambda p: opened.append(p) or real(p))
    res = file_search.search_files(query="plain", root="downloads", limit=50)
    assert len(res["results"]) >= 30
    assert not opened, "an ordinary file name is judged without opening the file"


def test_deny_list_does_not_re_resolve_for_every_file(test_home, monkeypatch):
    from pathlib import Path
    p = _write(test_home / "Documents" / "plain.txt", "x")
    cred.check(p, sniff=False)          # warm any cache
    calls = []
    real = Path.resolve
    monkeypatch.setattr(Path, "resolve",
                        lambda self, *a, **k: calls.append(1) or real(self, *a, **k))
    cred.check(p, sniff=False)
    assert len(calls) <= 3, f"{len(calls)} resolve() calls for one ordinary file"
