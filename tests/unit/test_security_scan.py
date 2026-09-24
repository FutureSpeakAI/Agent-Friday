"""The pre-commit secret scanner (.githooks/security_scan.py).

Every sample credential here is assembled at run time, so this file never
holds a string the scanner would match.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / ".githooks" / "security_scan.py"
_spec = importlib.util.spec_from_file_location("security_scan", _PATH)
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)


def _hits(line: str) -> list:
    out = []
    for category, rx, validator in scan.RULES:
        for m in rx.finditer(line):
            value = m.group(1) if m.groups() else m.group(0)
            if validator and not validator(value):
                continue
            out.append(category)
    return out


HEX32 = "0123456789abcdef" * 2
SAMPLES = {
    "twilio api key": "SK" + HEX32,
    "twilio account sid": "AC" + HEX32,
    "google client secret": "GOCSPX-" + "A1b2C3d4" * 3,
    "google refresh token": "1//0" + "gA1b2C3d4" * 4,
    "google access token": "ya29." + "A1b2C3d4" * 3,
    "hugging face": "hf_" + "A1b2C3d4" * 4,
    "groq": "gsk_" + "A1b2C3d4" * 6,
    "openrouter": "sk-or-v1-" + HEX32 * 2,
    "anthropic": "sk-ant-" + "A1b2C3d4" * 4,
    "github fine-grained": "github_pat_" + "A1b2C3d4" * 6,
    "jwt": "eyJ" + "hbGciOiJI" * 2 + ".eyJ" + "zdWIiOiIx" * 2 + "." + "SflKxwRJ" * 2,
    "encrypted key": "-----BEGIN ENCRYPTED " + "PRIVATE KEY-----",
}


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_each_credential_shape_is_caught(name):
    assert _hits(f'value = "{SAMPLES[name]}"'), name


def test_home_paths_are_caught_in_every_spelling():
    who = "jd" + "oe"
    for p in (f"C:\\Users\\{who}\\x", f"C:/Users/{who}/x", f"/c/Users/{who}/x"):
        assert "Private Windows user path" in _hits(p), p
    for p in ("C:\\Users\\you\\x", "C:/Users/x/.friday", "C:\\Users\\runneradmin\\x"):
        assert "Private Windows user path" not in _hits(p), p


def test_test_phone_numbers_are_not_people():
    assert not _hits("call +1" + "5005550006")
    assert _hits("call +1" + "5128675309")


def test_findings_are_masked():
    secret = SAMPLES["anthropic"]
    masked = scan.mask(secret)
    assert secret not in masked and masked.startswith(secret[:4])


@pytest.mark.parametrize("path", [".env", "config/.env.local", "keys/server.pem",
                                  "a/b/client_secret_123.json", "vault.enc", "id_ed25519",
                                  "trace.har"])
def test_sensitive_file_names_are_refused(path):
    assert scan.SENSITIVE_FILE_RE.search(path)


@pytest.mark.parametrize("path", ["src/agent_friday/services/credential_store.py",
                                  "docs/security/keys.md", "tests/unit/test_env.py"])
def test_ordinary_file_names_pass(path):
    assert not scan.SENSITIVE_FILE_RE.search(path)


def test_a_scanner_failure_holds_the_commit(monkeypatch):
    import runpy
    import subprocess as sp

    def boom(*a, **k):
        raise RuntimeError("git unavailable")

    monkeypatch.setattr(sp, "run", boom)
    with pytest.raises(SystemExit) as e:
        runpy.run_path(str(_PATH), run_name="__main__")
    assert e.value.code != 0


def test_the_machines_own_username_is_caught_at_commit_time(monkeypatch):
    monkeypatch.setenv("USERNAME", "jdoe" + "test")
    assert "jdoetest" in scan._machine_identity()
