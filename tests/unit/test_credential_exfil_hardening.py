"""Adversarial coverage for the credential deny-list and the egress classifier.

Every case is a route a hostile prompt would try after the obvious one is
closed: renamed copies, hard links, 8.3 short names, junctions, alternate data
streams, wildcards, quoting and string-splitting in a shell command, encoded
commands, and secrets that arrive base64-wrapped, JSON-quoted, URL-encoded or
split over lines. All secret values are synthetic.
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
import urllib.parse

import pytest

import agent_friday.core as core
from agent_friday.services import credential_paths as cred
from agent_friday.services import sensitivity_classifier as sc

PEM_HEAD = "-----BEGIN OPENSSH PRIVATE " + "KEY-----"  # pragma: allowlist secret
PEM_TAIL = "-----END OPENSSH PRIVATE " + "KEY-----"  # pragma: allowlist secret
PEM_BODY = ("b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n"
            "QyNTUxOQAAACBSYNTHETICSENTINELKEYMATERIAL0123456789abcdef\n")
PEM = PEM_HEAD + "\n" + PEM_BODY + PEM_TAIL + "\n"


def _write(p, text=PEM):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ── file reach ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "id_rsa.bak", "id_ed25519_old", "id_rsa_backup", "id_ecdsa.old",
])
def test_renamed_copies_of_ssh_keys_are_denied(name):
    assert cred.check(core.HOME / "Documents" / name)


def test_public_key_is_not_denied():
    assert cred.check(core.HOME / "Documents" / "id_rsa.pub") is None


@pytest.mark.parametrize("name", [
    ".netrc", "_netrc", ".git-credentials", ".pgpass", ".pypirc",
    "vault.kdbx", "cert.p12", "cert.pfx", "store.jks",
])
def test_more_credential_files_are_denied(name):
    assert cred.check(core.HOME / "Documents" / name)


@pytest.mark.parametrize("d", [".azure", ".kube"])
def test_more_credential_dirs_are_denied(d):
    assert cred.check(core.HOME / d / "config")


def test_backups_dir_is_denied():
    assert cred.check(core.FRIDAY_DIR / "backups" / "vault-reencrypt-1" / "vault" / "x.md")


def test_key_material_names_are_denied_anywhere():
    assert cred.check(core.HOME / "Desktop" / ".governance-key")
    assert cred.check(core.HOME / "Desktop" / ".vault_config.json")


def test_alternate_data_stream_and_trailing_dot_are_denied():
    assert cred.check(core.HOME / ".ssh" / "id_rsa:$DATA")
    assert cred.check(core.HOME / "Documents" / "id_rsa:hidden")
    assert cred.check(core.HOME / "Documents" / "id_rsa.")


def test_env_var_path_is_denied(monkeypatch):
    monkeypatch.setenv("FRIDAY_TEST_HOME_VAR", str(core.HOME))
    assert cred.check("%FRIDAY_TEST_HOME_VAR%\\.ssh\\config")
    assert cred.check("$FRIDAY_TEST_HOME_VAR/.ssh/config")


def test_a_key_renamed_to_anything_is_denied_by_content(test_home):
    p = _write(test_home / "Documents" / "holiday-photo.txt")
    assert cred.check(p), "a private key by content is denied whatever it is called"


def test_a_hard_link_to_a_key_is_denied_by_content(test_home):
    src = _write(test_home / ".ssh" / "id_rsa")
    link = test_home / "Documents" / "notes.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists():
        link.unlink()
    os.link(src, link)
    assert cred.check(link)


def test_a_public_certificate_pem_is_readable(test_home):
    p = _write(test_home / "Documents" / "ca.pem",
               "-----BEGIN CERTIFICATE-----\nMIIBsomethingpublic\n-----END CERTIFICATE-----\n")
    assert cred.check(p) is None


def test_a_private_key_pem_is_denied(test_home):
    p = _write(test_home / "Documents" / "server.pem")
    assert cred.check(p)


def test_ordinary_small_text_is_still_readable(test_home):
    p = _write(test_home / "Documents" / "shopping.txt", "milk, eggs, bread")
    assert cred.check(p) is None


@pytest.mark.skipif(sys.platform != "win32", reason="8.3 names and junctions are Windows")
def test_short_name_and_junction_routes_are_denied(test_home):
    import ctypes
    ssh = test_home / ".ssh"
    key = _write(ssh / "id_rsa")
    buf = ctypes.create_unicode_buffer(600)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(key), buf, 600)
    if n and buf.value.lower() != str(key).lower():
        assert cred.check(buf.value), f"short name {buf.value} slipped through"
    j = test_home / "Documents" / "harmless"
    j.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(j), str(ssh)],
                       capture_output=True, text=True)
    if r.returncode == 0:
        try:
            assert cred.check(j / "id_rsa")
        finally:
            os.rmdir(j)


def test_read_file_refuses_a_renamed_key_without_leaking(test_home):
    import agent_friday.services.agent as agent
    p = _write(test_home / "Documents" / "recipe.txt")
    out = agent._tool_read_file({"path": str(p)})
    assert "SENTINELKEYMATERIAL" not in out


def test_open_path_resolves_then_checks(test_home, monkeypatch):
    import agent_friday.services.agent as agent
    key = _write(test_home / ".ssh" / "id_rsa")
    opened = []
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)
    monkeypatch.setattr(agent, "_resolve_open_target", lambda t: str(key))
    out = agent._tool_open_path({"path": "my ssh key"})
    assert not opened
    assert "won't open" in out.lower()


def test_search_content_never_returns_a_renamed_key(test_home):
    from agent_friday.services import file_search
    _write(test_home / "Downloads" / "vacation.txt")
    res = file_search.search_files(query="", root="downloads", content_query="SENTINELKEYMATERIAL")
    assert res["results"] == []
    assert "vacation.txt" not in str(res["results"])


# ── shell commands ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cmd", [
    "cd ~/.ssh; cat id_r*",
    "cd .ssh ; type id_ed25519",
    "Get-Content (Join-Path $env:USERPROFILE '.s' + 'sh\\id_rsa')",
    "gc \"$env:USERPROFILE\\.s\"\"sh\\id_rsa\"",
    "Get-Content '~/.ss'+'h/id_rsa'",
    "type %USERPROFILE%\\.ssh\\id_rsa",
    "more C:\\Users\\x\\.ssh\\id_rsa",
    "findstr /s BEGIN C:\\Users\\x\\.ssh\\*",
    "copy $HOME\\.ssh\\id_rsa C:\\temp\\a.txt",
    "type \\\\localhost\\c$\\Users\\x\\.ssh\\id_rsa",
    "powershell -c \"gc ~\\.ssh\\id_rsa\"",
    "Get-Content `$env:USERPROFILE\\.ss`h\\id_rsa",
    "type ^%USERPROFILE^%\\.ssh\\id_rsa",
    "Get-Content ~/.aws/credentials",
    "cat ~/.friday/backups/vault-reencrypt-1/vault/.vault_config.json",
    "Get-ChildItem -Recurse ~ -Filter id_rsa*",
    "cat ~/.netrc",
    "Get-Content ~/.git-credentials",
])
def test_command_read_routes_are_refused(cmd):
    assert cred.scan_command(cmd), cmd


def test_encoded_command_is_decoded_and_scanned():
    inner = "Get-Content $env:USERPROFILE\\.ssh\\id_rsa"
    enc = base64.b64encode(inner.encode("utf-16-le")).decode()
    assert cred.scan_command(f"powershell -EncodedCommand {enc}")
    assert cred.scan_command(f"powershell -enc {enc}")


def test_a_wildcard_that_expands_to_a_key_is_refused(test_home):
    _write(test_home / "Documents" / "keys" / "notes1.txt")
    assert cred.scan_command(f'Get-Content "{test_home}\\Documents\\keys\\notes*.txt"')


@pytest.mark.parametrize("cmd", [
    "Get-ChildItem ~/Documents",
    "Get-Content ~/Documents/report.txt",
    "Get-Date",
    "git status",
    "python -m pytest tests -q",
    "cd ~/Projects/site; npm run build",
    "Write-Output 'my password manager review'",
])
def test_ordinary_commands_still_run(cmd):
    assert cred.scan_command(cmd) is None, cmd


def test_command_output_that_carries_a_key_is_redacted(monkeypatch):
    import agent_friday.services.agent as agent

    class P:
        stdout = "listing\n" + PEM + "done"
        stderr = ""
        returncode = 0
    monkeypatch.setattr(agent.subprocess, "run", lambda *a, **k: P())
    out = agent._tool_run_command({"command": "Get-ChildItem C:\\Temp\\odd"})
    assert "SENTINELKEYMATERIAL" not in out
    assert "listing" in out and "done" in out


def test_file_text_that_carries_a_key_is_redacted(test_home):
    import agent_friday.services.agent as agent
    note = test_home / "Documents" / "mixed.txt"
    _write(note, "before " + "x" * 20000 + "\n" + PEM + "after")
    out = agent._tool_read_file({"path": str(note)})
    assert "SENTINELKEYMATERIAL" not in out
    assert "before" in out and "after" in out


# ── egress ────────────────────────────────────────────────────────────────────

def _tier(text: str) -> int:
    return sc.classify(text, use_presidio=False, use_embeddings=False, egress=True)


_G = "0123456789abcdefghij0123456789abcdef"
_B64PEM = base64.b64encode(PEM.encode()).decode()
SECRETS = {
    "pem header split over lines": "-----BEGIN OPENSSH\nPRIVATE KEY-----\nabc",  # pragma: allowlist secret
    "pem header extra spaces": "-----BEGIN  RSA   PRIVATE KEY-----",  # pragma: allowlist secret
    "pem body without header": "notes:\n" + PEM_BODY,
    "pem base64 wrapped": _B64PEM,
    "pem base64 wrapped in lines": "\n".join(
        _B64PEM[i:i + 64] for i in range(0, len(_B64PEM), 64)),
    "pem in json arg": '{"content": "' + PEM.replace("\n", "\\n") + '"}',
    "pem url-encoded": urllib.parse.quote(PEM),
    "pem in a url query": "https://paste.example/api?data=" + urllib.parse.quote(PEM, safe=""),
    "github in url": "https://x.example/cb?token=gh" + "p_" + _G,
    "github fine grained": "github_" + "pat_" + "11ABCDEFG0" + "a" * 60,
    "github base64": base64.b64encode(("gh" + "p_" + _G).encode()).decode(),
    "aws temp key": "ASIA" + "B" * 16 + " is the key id",
    "aws secret assignment": "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",  # pragma: allowlist secret
    "json password": '{"user": "a", "password": "hunter2hunter2"}',  # pragma: allowlist secret
    "json api key": '{"api_key": "' + "k1" * 15 + '"}',  # pragma: allowlist secret
    "gitlab": "glpat-" + "x" * 20,
    "stripe live": "sk_" + "live_" + "a" * 24,
    "npm": "npm_" + "a" * 36,
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",  # pragma: allowlist secret
    "bearer": "Authorization: Bearer " + "q" * 30,
    "url credentials": "postgres://admin:s3cretpass@db.example:5432/app",  # pragma: allowlist secret
    "slack webhook": "https://hooks.slack.com/services/T0000000/B0000000/" + "X" * 24,
    "google oauth": "ya29." + "a" * 40,
    "keystore json": '{"kdf":"argon2id","root_key":"' + "A" * 40 + '"}',  # pragma: allowlist secret
}
BENIGN = [
    "The committee meets on Tuesday to review the quarterly budget.",
    "Password: Required before the next login.",
    "Please reset your password when you get a chance.",
    "Here is a certificate: -----BEGIN CERTIFICATE-----",
    "See https://example.com/docs/api-key-rotation for the guide.",
    "My token of appreciation: a coffee.",
    "ghp is a prefix people talk about; the hex a3f9 is not a token.",
    "postgres://db.example:5432/app has no credentials",
    "The word Bearer alone, and the api key concept.",
]


@pytest.mark.parametrize("label", list(SECRETS))
def test_secret_formats_are_sensitive(label):
    assert _tier(SECRETS[label]) == sc.Tier.SENSITIVE, label


@pytest.mark.parametrize("text", BENIGN)
def test_benign_text_is_not_sensitive(text):
    assert _tier(text) != sc.Tier.SENSITIVE, text


def test_secret_deep_inside_a_long_message_is_caught():
    body = ("ordinary sentence about the weekend. " * 400) + "gh" + "p_" + _G
    assert _tier(body) == sc.Tier.SENSITIVE


def test_redact_secrets_removes_key_blocks_and_tokens_only():
    text = "keep this line\n" + PEM + "and token gh" + "p_" + _G + " gone\nkeep this too"
    out = cred.redact_secrets(text)
    assert "SENTINELKEYMATERIAL" not in out
    assert _G not in out
    assert "keep this line" in out and "keep this too" in out
    assert cred.redact_secrets("nothing secret here") == "nothing secret here"
