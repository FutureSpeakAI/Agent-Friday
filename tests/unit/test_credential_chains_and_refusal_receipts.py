"""Command chains, the one definition of a secret, the app's own secret files,
the durable refusal receipt, bounded detection work and names-only listings.

All values are synthetic and assembled at run time.
"""
from __future__ import annotations

import base64
import json
import os
import random
import re
import secrets
import time
from pathlib import Path

import pytest

from agent_friday.services import credential_paths as cred
from agent_friday.services import secret_patterns as sp

PEM = ("-----BEGIN OPENSSH PRIVATE " + "KEY-----\n"  # pragma: allowlist secret
       "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n"
       "-----END OPENSSH PRIVATE " + "KEY-----\n")  # pragma: allowlist secret
ANTHROPIC = "sk-" + "ant-api03-" + "Zq7" * 12  # pragma: allowlist secret
GEMINI = "AI" + "za" + "SyD4" + "x" * 31  # pragma: allowlist secret
AWS_SECRET = "wJalrXUtnFEMI/K7MDENG" + "/bPxRfiCYEXAMPLEKEY"  # pragma: allowlist secret


def _mk(p: Path, body="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


@pytest.fixture
def profile(test_home, monkeypatch):
    _mk(test_home / ".ssh" / "id_ed25519", PEM)
    _mk(test_home / ".ssh" / "config", "Host box\n  HostName 10.0.0.2\n")
    _mk(test_home / ".aws" / "credentials", "[default]\naws_access_key_id = x\n")
    _mk(test_home / ".kube" / "config", "apiVersion: v1\n")
    _mk(test_home / "Projects" / "site" / "app.py", "print('hi')\n")
    _mk(test_home / "Projects" / "site" / "deep" / "more" / "x.txt", "x\n")
    monkeypatch.chdir(test_home / "Projects" / "site")
    return test_home


# ── a named folder is the root of its own statement only ─────────────────────

@pytest.mark.parametrize("cmd", [
    "Get-ChildItem Projects; Get-ChildItem -Recurse | Get-Content",
    "ls Projects && ls -R | xargs cat",
    "dir Projects & Get-ChildItem -Recurse | Get-Content",
    "Get-ChildItem Projects || Get-ChildItem -Recurse | Get-Content",
    "Get-ChildItem Projects\nGet-ChildItem -Recurse | Get-Content",
])
def test_a_folder_named_in_one_statement_is_not_the_root_of_the_next(
        profile, monkeypatch, cmd):
    monkeypatch.chdir(profile)
    assert cred.scan_command(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "Get-ChildItem Projects -Recurse | Get-Content",
    "Get-ChildItem Projects | Get-ChildItem -Recurse | Get-Content",
    "ls Projects; ls Projects -R | xargs cat",
])
def test_a_folder_named_in_the_same_pipeline_is_still_its_root(
        profile, monkeypatch, cmd):
    monkeypatch.chdir(profile)
    assert cred.scan_command(cmd) is None, cmd


# ── cd, Set-Location and pushd move where a rootless walk starts ─────────────

@pytest.mark.parametrize("cmd", [
    "cd ..; cd ..; Get-ChildItem -Recurse | Get-Content",
    "Set-Location ..\\..; Get-ChildItem -Recurse | Get-Content",
    "pushd ../..; ls -R | xargs cat",
    "cd ..; cd ..; 7z a out.7z .",
    "cd ..\\.. && dir /s | findstr x",
    "sl ~; Get-ChildItem -Recurse | Get-Content",
])
def test_a_walk_after_cd_starts_where_the_chain_has_moved_to(profile, cmd):
    assert cred.scan_command(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "cd deep; Get-ChildItem -Recurse | Get-Content",
    "cd deep; cd more; ls -R | xargs cat",
    "Set-Location .\\deep; Get-ChildItem -Recurse | Get-Content",
])
def test_a_walk_after_cd_into_a_project_subfolder_runs(profile, cmd):
    assert cred.scan_command(cmd) is None, cmd


# ── kubectl config view prints the credential ────────────────────────────────

@pytest.mark.parametrize("cmd", [
    "kubectl --kubeconfig ~/.kube/config config view --raw",
    "kubectl config view --raw",
    "kubectl --kubeconfig=~/.kube/config config view --flatten",
    "kubectl config view --raw=true -o yaml",
    "sudo kubectl --kubeconfig ~/.kube/config config view --minify --raw",
])
def test_kubectl_config_view_raw_reads_the_kubeconfig(profile, cmd):
    assert cred.scan_command(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "kubectl --kubeconfig ~/.kube/config get pods",
    "kubectl --kubeconfig=~/.kube/config config current-context",
    "kubectl config view",
])
def test_kubectl_that_only_uses_or_shows_redacted_config_runs(profile, cmd):
    assert cred.scan_command(cmd) is None, cmd


# ── one definition of what a secret looks like ───────────────────────────────

def _jwt():
    enc = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    return ".".join([enc(b'{"alg":"RS256","kid":"abc"}'),
                     enc(b'{"iss":"kubernetes/serviceaccount","sub":"system:serviceaccount:d:x"}'),
                     enc(random.Random(48).randbytes(48))])


@pytest.mark.parametrize("label,value", [
    ("aws secret, ini", "aws_secret_access_key = " + AWS_SECRET),
    ("aws secret, json", '"SecretAccessKey": "' + AWS_SECRET + '"'),
    ("aws secret, env", "AWS_SECRET_ACCESS_KEY=" + AWS_SECRET),
    ("jwt", "token: " + _jwt()),
    ("jwt, spaced json payload", "bearer " + base64.urlsafe_b64encode(b'{"alg":"HS256"}').decode().rstrip("=")
     + "." + base64.urlsafe_b64encode(b'{ "sub": "1234567890" }').decode().rstrip("=")
     + ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV"),
    ("anthropic", "key=" + ANTHROPIC),
    ("gemini", "GOOGLE_API_KEY=" + GEMINI),
])
def test_redaction_withholds_what_the_egress_classifier_calls_a_secret(label, value):
    out = sp.redact("before\n" + value + "\nafter")
    assert out.count(sp.WITHHELD) >= 1 and "before" in out and "after" in out, (label, out)
    secret = re.findall(r"[A-Za-z0-9_+/=.-]{30,}", value)[-1]
    assert secret not in out, (label, out)
    assert sp.contains_secret(value), label


def test_the_provider_key_shapes_are_defined_once():
    from agent_friday.services import secret_shapes, sensitivity_classifier as sc
    for sid, _label, pat, _target in secret_shapes.SHAPES:
        sample = {
            "anthropic": ANTHROPIC, "openrouter": "sk-" + "or-v1-" + "a1" * 20,
            "openai": "sk-" + "proj-" + "a1" * 20, "gemini": GEMINI,
            "gemini_aq": "AQ." + "Ab1" * 10, "google_client_secret": "GOCSPX-" + "a1" * 14,
            "google_refresh": "1//0" + "a1" * 20, "google_access": "ya29." + "a1" * 14,
            "aws": "AKIA" + "ABCDEFGH12345678", "slack": "xoxb-" + "1234567890" * 2,
            "github": "ghp_" + "a1" * 20, "huggingface": "hf_" + "a1" * 20,
            "groq_xai_pplx": "gsk_" + "a1" * 25, "twilio": "SK" + "0123456789abcdef" * 2,
            "jwt": _jwt(), "private_key": "-----BEGIN RSA PRIVATE " + "KEY-----",
            "elevenlabs": "sk_" + "0123456789abcdef" * 3, "firecrawl": "fc-" + "0123456789abcdef" * 2,
            "telegram": "123456789:" + "Ab1" * 11 + "xy", "discord": "M" + "a1" * 12 + "x." + "Ab1234" + "." + "a1" * 14,
            "linear": "lin_api_" + "a1" * 20, "notion": "ntn_" + "a1" * 25,
        }[sid]
        assert sp.contains_secret(sample), sid
        assert sample not in sp.redact("x " + sample + " y") or sid == "private_key", sid
    # the classifier's key pattern is built from the shape list, not retyped
    for sid, _l, pat, _t in secret_shapes.SHAPES:
        if sid in ("anthropic", "openai", "gemini", "gemini_aq"):
            assert pat in sc._API_KEY_RE.pattern, sid


# ── the app's own legacy secret files ────────────────────────────────────────

@pytest.fixture
def app_dir(test_home, monkeypatch):
    d = test_home / "friday_app"
    d.mkdir(exist_ok=True)
    _mk(d / ".env", "ANTHROPIC_API_KEY=" + ANTHROPIC + "\n")
    _mk(d / "secrets.yaml", "gemini: " + GEMINI + "\n")
    monkeypatch.setattr(cred, "_app_dir", lambda: d, raising=False)
    cred._deny_norms.cache_clear()
    yield d
    cred._deny_norms.cache_clear()


@pytest.mark.parametrize("name", [".env", "secrets.yaml"])
def test_the_apps_own_legacy_secret_files_are_denied(app_dir, name):
    assert cred.check(app_dir / name), name


def test_a_project_env_file_elsewhere_is_not_the_apps_secret_file(profile, app_dir):
    other = _mk(profile / "Projects" / "site" / ".env", "DEBUG=1\n")
    assert cred.check(other) is None


@pytest.mark.parametrize("tool,args", [
    ("read_file", lambda d: {"path": str(d / ".env")}),
    ("read_file", lambda d: {"path": str(d / "secrets.yaml")}),
    ("run_command", lambda d: {"command": f"type {d}\\.env"}),
    ("run_command", lambda d: {"command": f"cat {d}/secrets.yaml"}),
])
def test_the_tools_return_a_refusal_not_the_apps_keys(app_dir, tool, args):
    from agent_friday.services import agent
    out = agent._execute_tool(tool, args(app_dir), session_ctx={"authenticated": True})
    assert out.startswith("I won't"), out
    assert ANTHROPIC not in out and GEMINI not in out


def test_a_bare_name_in_the_app_folder_is_refused_in_a_command(app_dir, monkeypatch):
    monkeypatch.chdir(app_dir)
    assert cred.scan_command("type .env")
    assert cred.scan_command("cd ..; cd friday_app; cat secrets.yaml")


def test_output_that_reaches_the_model_has_the_provider_keys_withheld():
    out = sp.redact("ANTHROPIC_API_KEY=" + ANTHROPIC + "\ngemini: " + GEMINI + "\n")
    assert ANTHROPIC not in out and GEMINI not in out


# ── a refusal is a signed receipt on disk, written before the denial ─────────

def _bom(test_home):
    p = test_home / ".friday" / "decision-bom.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


@pytest.mark.parametrize("tool,args", [
    ("read_file", lambda h: {"path": str(h / ".ssh" / "id_ed25519")}),
    ("open_path", lambda h: {"path": str(h / ".ssh" / "id_ed25519")}),
    ("run_command", lambda h: {"command": "Get-ChildItem $HOME -Recurse | Get-Content"}),
    ("run_command", lambda h: {"command": f"Get-Content {h}\\.aws\\credentials"}),
    ("run_sandboxed", lambda h: {"code": f"print(open(r'{h}/.ssh/id_ed25519').read())"}),
])
def test_every_credential_refusal_writes_a_signed_deny_entry(profile, monkeypatch, tool, args):
    from agent_friday.governance import action_gate
    from agent_friday.services import agent
    monkeypatch.chdir(profile)
    before = len(_bom(profile))
    out = agent._execute_tool(tool, args(profile), session_ctx={"authenticated": True})
    assert out.startswith("I won't"), out
    fresh = [e for e in _bom(profile)[before:] if e.get("kind") == "credential_refusal"]
    assert len(fresh) == 1, _bom(profile)[before:]
    e = fresh[0]
    assert e["decision"] == "deny" and e["tool"] == tool
    assert action_gate.verify_receipt(e), "the entry is signed"
    raw = json.dumps(e)
    assert "BEGIN" not in raw and "b3BlbnNz" not in raw, "no key material in the entry"


def test_an_ordinary_read_writes_no_refusal_entry(profile):
    from agent_friday.services import agent
    f = _mk(profile / "Projects" / "site" / "notes.txt", "milk")
    before = len(_bom(profile))
    agent._execute_tool("read_file", {"path": str(f)}, session_ctx={"authenticated": True})
    assert not [e for e in _bom(profile)[before:] if e.get("kind") == "credential_refusal"]


def test_the_refusal_is_still_returned_when_the_receipt_cannot_be_written(profile, monkeypatch):
    from agent_friday.governance import action_gate
    from agent_friday.services import agent
    monkeypatch.setattr(action_gate, "_receipt",
                        lambda e: (_ for _ in ()).throw(OSError("disk full")))
    out = agent._execute_tool("read_file", {"path": str(profile / ".ssh" / "id_ed25519")},
                              session_ctx={"authenticated": True})
    assert out.startswith("I won't"), out


# ── detection on the mandatory egress path does bounded work ─────────────────

def _bodies():
    return {
        "hex": random.Random(7).randbytes(100_000).hex(),
        "base64": base64.b64encode(random.Random(11).randbytes(150_000)).decode(),
        "alnum": "a1" * 100_000,
    }


@pytest.mark.parametrize("kind", ["hex", "base64", "alnum"])
def test_a_200kb_body_is_scanned_with_bounded_work(kind):
    text = _bodies()[kind]
    sp.reset_work()
    t = time.process_time()
    assert not sp.contains_secret(text)
    cpu = time.process_time() - t
    # Work is counted in characters handed to a pattern, so the bound does not
    # move with machine load; the CPU-time bound is a generous second guard.
    assert sp.work_chars() <= 24 * len(text), (kind, sp.work_chars(), len(text))
    assert cpu < 3.0, f"{kind}: {cpu:.2f}s CPU"


def test_the_work_bound_does_not_blind_the_scan():
    body = secrets.token_hex(100_000) + "\n" + "token: " + _jwt() + "\n" + "a1" * 1000
    assert sp.contains_secret(body)
    assert sp.contains_secret("x" * 100_000 + " password=hunter2x9 " + "y" * 100_000)


# ── a listing of names is not a read ─────────────────────────────────────────

@pytest.mark.parametrize("cmd", [
    "ls ~/.ssh",
    "ls -la ~/.ssh",
    "Get-ChildItem ~/.ssh",
    "Get-ChildItem -Force $HOME\\.ssh",
    "dir %USERPROFILE%\\.ssh",
    "gci ~/.aws",
])
def test_listing_the_names_in_a_credential_folder_runs(profile, cmd):
    assert cred.scan_command(cmd) is None, cmd


@pytest.mark.parametrize("cmd", [
    "ls ~/.ssh | xargs cat",
    "Get-ChildItem ~/.ssh | Get-Content",
    "Get-ChildItem ~/.ssh | ForEach-Object { [IO.File]::ReadAllText($_.FullName) }",
    "Get-ChildItem ~/.ssh | % { $_.OpenText().ReadToEnd() }",
    "ls ~/.ssh; cat ~/.ssh/id_ed25519",
    "ls -R ~/.ssh",
    "Get-ChildItem ~/.ssh -Recurse",
    "cat ~/.ssh/id_ed25519",
    "ls ~/.ssh > $env:TEMP\\k.txt",
    "ls $(cat ~/.ssh/id_ed25519)",
])
def test_reading_or_piping_from_a_credential_folder_stays_refused(profile, cmd):
    assert cred.scan_command(cmd), cmd
