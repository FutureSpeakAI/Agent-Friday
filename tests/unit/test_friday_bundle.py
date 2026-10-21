"""The Friday Bundle: one encrypted backup that restores onto a clean machine.

North star §27.1 (carry Friday's durable self to another device), §27.2
(encrypted, versioned, selectable categories, credentials excluded), §27.4
(verify by test-reading), §27.6 (the restore steps; outward actions held until
re-established on the device), §34.3-7 (restore from a portable bundle);
burn-down BD-17.

Red on the old code by assertion: `friday bundle` and `friday restore` did not
exist (the command line refused them), and `friday export --full` had no way
back in. The rest pins the bundle itself.
"""
from __future__ import annotations

import io
import json
import os
import struct
import sys
import tarfile
from pathlib import Path

import pytest

from agent_friday.governance import proof_of_integrity as poi

PASS = "correct horse battery staple 42"   # pragma: allowlist secret
VAULT_PASS = "vault passphrase for the test"  # pragma: allowlist secret


def _cli(argv, monkeypatch, env=None):
    from agent_friday import cli
    monkeypatch.setattr(sys, "argv", ["friday"] + argv)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    with pytest.raises(SystemExit) as e:
        cli.main()
    return e.value.code


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """Machine A's data folder, seeded like a real one, and machine B's empty one."""
    a, b = tmp_path / "a" / ".friday", tmp_path / "b" / ".friday"
    a.mkdir(parents=True)
    monkeypatch.setenv("FRIDAY_HOME", str(a))
    monkeypatch.setenv("FRIDAY_LEDGER_DIR", str(a / "ledger"))
    monkeypatch.setenv("FRIDAY_VAULT_PASSPHRASE", VAULT_PASS)
    monkeypatch.setenv("FRIDAY_PASSWORD", VAULT_PASS)
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", a / "vault" / ".governance-key")
    from agent_friday.services import vault_passphrase
    vault_passphrase.reset_cache()
    try:
        from agent_friday.services import action_ledger
        action_ledger.reset_caches()
    except ImportError:                   # before the action ledger existed
        action_ledger = None
    from agent_friday.privacy import vault_crypto as vc
    salt = os.urandom(16)
    (a / "vault").mkdir()
    (a / "vault" / ".vault_config.json").write_text(json.dumps({"salt_hex": salt.hex()}))
    key = vc.derive_key(VAULT_PASS, salt, vc.FAST_PROFILE)
    (a / "vault" / "notes.enc").write_bytes(vc.encrypt(b"the vault holds this", key))
    files = {
        "settings.json": json.dumps({"agent_name": "Friday"}),
        "core_memory/ME.md": "- likes long walks\n",
        "memory/memory_ledger.jsonl": '{"x":1}\n',
        "skills/my-skill/SKILL.md": "---\nname: my-skill\n---\nDo the thing.\n",
        "workspaces/ws1/manifest.json": json.dumps({"id": "ws1"}),
        "conversations/c1.json": json.dumps({"turns": ["hi"]}),
        "wiki/page.md": "# A page\n",
        # never carried:
        "providers/keys/anthropic.key": "sk-placeholder-not-real",   # pragma: allowlist secret
        "google_accounts/tokens/me.token.enc": "token-placeholder",
        "security/vault-passphrase.dpapi": "dpapi-placeholder",
        "secret_key": "session-secret-placeholder",
        "tls/key.pem": "pem-placeholder",
        "runtime/models/big.gguf": "x" * 1000,
        "logs/crashes.log": "nothing",
        "friday_server.port": "3000",
    }
    for rel, text in files.items():
        p = a / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (a / "security" / "keystore.json").write_text(json.dumps({"wrap": "none", "v": 1}))
    if action_ledger is not None:
        action_ledger.append("decision", {"tool": "probe_send", "decision": "allow"}, durable=True)
        action_ledger.append("activity", {"kind": "tool_call", "tool": "probe"})
    return a, b, key


def _make(a, out):
    from agent_friday.privacy import vault_crypto as vc
    from agent_friday.services import friday_bundle as fb
    return fb.create(out, PASS, home=a, kdf_profile=vc.FAST_PROFILE)


def _point_at(home, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    monkeypatch.setenv("FRIDAY_LEDGER_DIR", str(home / "ledger"))
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", home / "vault" / ".governance-key")
    try:
        from agent_friday.services import action_ledger
        action_ledger.reset_caches()
    except ImportError:
        pass


def test_the_command_line_makes_verifies_and_restores_a_bundle(homes, tmp_path, monkeypatch):
    a, b, _key = homes
    out = tmp_path / "out" / "mine.fridaybundle"
    env = {"FRIDAY_BUNDLE_PASSPHRASE": PASS}
    assert _cli(["bundle", "create", "--out", str(out)], monkeypatch, env) == 0
    assert out.exists() and out.read_bytes()[:12] == b"FRIDAYBUNDLE"
    assert _cli(["bundle", "verify", str(out)], monkeypatch, env) == 0
    _point_at(b, monkeypatch)
    assert _cli(["restore", str(out), "--home", str(b)], monkeypatch, env) == 0
    assert (b / "core_memory" / "ME.md").read_text(encoding="utf-8") == "- likes long walks\n"


def test_a_bundle_restores_onto_a_clean_machine_with_everything_that_should_travel(homes, tmp_path, monkeypatch):
    from agent_friday.privacy import vault_crypto as vc
    from agent_friday.services import friday_bundle as fb
    a, b, key = homes
    out = tmp_path / "x.fridaybundle"
    m = _make(a, out)
    cats = {f["category"] for f in m["files"]}
    assert {"memory", "vault", "settings", "skills", "workspaces", "receipts", "conversations"} <= cats
    assert set(m["secrets"]) == {"vault-passphrase", "governance-key"}

    _point_at(b, monkeypatch)
    r = fb.restore(out, PASS, home=b)
    # what travels
    for rel in ("settings.json", "core_memory/ME.md", "skills/my-skill/SKILL.md",
                "workspaces/ws1/manifest.json", "conversations/c1.json", "wiki/page.md",
                "security/keystore.json", "vault/.vault_config.json"):
        assert (b / rel).read_bytes() == (a / rel).read_bytes(), rel
    assert vc.decrypt((b / "vault" / "notes.enc").read_bytes(), key) == b"the vault holds this"
    # what never travels
    for rel in ("providers/keys/anthropic.key", "google_accounts/tokens/me.token.enc",
                "security/vault-passphrase.dpapi", "secret_key", "tls/key.pem",
                "runtime/models/big.gguf", "logs/crashes.log", "friday_server.port"):
        assert not (b / rel).exists(), rel
    # the receipts still verify on the new machine, with the restore on record
    assert r["checked_in_place"]["files_match"] is True
    assert r["checked_in_place"]["ledger"]["ok"] is True, r["checked_in_place"]
    from agent_friday.services import action_ledger
    kinds = [e["kind"] for e in action_ledger.entries(newest_first=False)]
    assert "decision" in kinds and kinds[-1] == "restore"
    assert action_ledger.verify()["ok"]
    assert (b / "vault" / ".governance-key").read_bytes() == (a / "vault" / ".governance-key").read_bytes()


def test_after_a_restore_outward_actions_wait_for_the_owner(homes, tmp_path, monkeypatch):
    from agent_friday.governance import action_gate
    from agent_friday.services import friday_bundle as fb
    a, b, _ = homes
    out = tmp_path / "x.fridaybundle"
    _make(a, out)
    _point_at(b, monkeypatch)
    monkeypatch.setattr(action_gate, "friday_home", lambda: str(b))
    fb.restore(out, PASS, home=b)
    v = action_gate.authorize("create_calendar_event", {"title": "x"}, {"is_background_task": True})
    assert v.action == "deny" and "restored" in v.reason
    assert fb.resume_after_restore("owner:test")
    v = action_gate.authorize("create_calendar_event", {"title": "x"}, {"is_background_task": True})
    assert v.action != "deny", v.reason


def test_a_wrong_passphrase_or_a_changed_byte_restores_nothing(homes, tmp_path, monkeypatch):
    from agent_friday.services import friday_bundle as fb
    a, b, _ = homes
    out = tmp_path / "x.fridaybundle"
    _make(a, out)
    _point_at(b, monkeypatch)
    with pytest.raises(fb.BundleError, match="Wrong passphrase"):
        fb.restore(out, "not the passphrase at all", home=b)
    data = bytearray(out.read_bytes())
    for where in (len(data) // 2, len(data) - 5):
        bad = bytearray(data)
        bad[where] ^= 0x01
        out.write_bytes(bytes(bad))
        with pytest.raises(fb.BundleError):
            fb.restore(out, PASS, home=b)
    out.write_bytes(bytes(data[:-100]))                       # cut off
    with pytest.raises(fb.BundleError):
        fb.verify(out, PASS)
    out.write_bytes(bytes(data) + b"more")                    # bytes added after the end
    with pytest.raises(fb.BundleError):
        fb.verify(out, PASS)
    assert not b.exists() or not any(b.iterdir())
    assert not list(b.parent.glob("*.friday-restore-*")), "a staging folder was left behind"


def test_a_folder_that_already_holds_a_friday_is_never_overwritten(homes, tmp_path, monkeypatch):
    from agent_friday.services import friday_bundle as fb
    a, b, _ = homes
    out = tmp_path / "x.fridaybundle"
    _make(a, out)
    (b / "core_memory").mkdir(parents=True)
    (b / "core_memory" / "ME.md").write_text("- someone else's Friday\n", encoding="utf-8")
    (b / "settings.json").write_text('{"mine": true}', encoding="utf-8")
    _point_at(b, monkeypatch)
    with pytest.raises(fb.BundleError, match="already has a Friday"):
        fb.restore(out, PASS, home=b)
    assert (b / "settings.json").read_text(encoding="utf-8") == '{"mine": true}'
    r = fb.restore(out, PASS, home=b, replace=True)
    aside = b.parent / r["moved_aside"]
    assert (aside / "settings.json").read_text(encoding="utf-8") == '{"mine": true}'
    assert json.loads((b / "settings.json").read_text(encoding="utf-8")) == {"agent_name": "Friday"}


def test_a_dry_run_writes_nothing_and_categories_can_be_left_out(homes, tmp_path, monkeypatch):
    from agent_friday.privacy import vault_crypto as vc
    from agent_friday.services import friday_bundle as fb
    a, b, _ = homes
    out = tmp_path / "x.fridaybundle"
    _make(a, out)
    _point_at(b, monkeypatch)
    r = fb.restore(out, PASS, home=b, dry_run=True)
    assert r["dry_run"] and not b.exists()
    small = tmp_path / "small.fridaybundle"
    m = fb.create(small, PASS, home=a, categories=["settings", "skills"], kdf_profile=vc.FAST_PROFILE)
    assert {f["category"] for f in m["files"]} == {"settings", "skills"}
    assert m["secrets"] == ["governance-key"]


def test_a_bundle_whose_entries_point_outside_the_folder_is_refused(homes, tmp_path, monkeypatch):
    """A bundle not made by Friday, with an entry that climbs out of the data
    folder, is refused before anything is written."""
    import base64
    import hashlib
    from agent_friday.privacy import vault_crypto as vc
    from agent_friday.services import friday_bundle as fb
    a, b, _ = homes
    salt, prefix = os.urandom(16), os.urandom(8)
    kdf = {"name": "argon2id", "t": 1, "m": 64, "p": 1, "salt": base64.b64encode(salt).decode()}
    header = {"format": fb.FORMAT, "bundle_id": "fb-evil", "kdf": kdf,
              "nonce_prefix": base64.b64encode(prefix).decode(), "categories": ["other"], "app": {}}
    raw = json.dumps(header, sort_keys=True).encode()
    head = fb.MAGIC + struct.pack(">I", len(raw)) + raw
    out = tmp_path / "evil.fridaybundle"
    with open(out, "wb") as f:
        f.write(head)
        w = fb._EncryptingWriter(f, vc.derive_key(PASS, salt, vc.FAST_PROFILE),
                                 hashlib.sha256(head).digest(), prefix)
        with tarfile.open(fileobj=w, mode="w|") as tar:
            ti = tarfile.TarInfo("home/../../escaped.txt")
            data = b"out of bounds"
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
        w.finish()
    _point_at(b, monkeypatch)
    with pytest.raises(fb.BundleError, match="should not"):
        fb.restore(out, PASS, home=b)
    assert not (tmp_path / "escaped.txt").exists() and not (b.parent / "escaped.txt").exists()


def test_no_bundle_inside_the_data_folder_and_no_short_passphrase(homes):
    from agent_friday.privacy import vault_crypto as vc
    from agent_friday.services import friday_bundle as fb
    a, _b, _ = homes
    with pytest.raises(fb.BundleError, match="inside"):
        fb.create(a / "x.fridaybundle", PASS, home=a, kdf_profile=vc.FAST_PROFILE)
    with pytest.raises(fb.BundleError, match="12"):
        fb.create(a.parent / "x.fridaybundle", "short", home=a, kdf_profile=vc.FAST_PROFILE)


def test_restore_refuses_while_friday_runs_on_that_folder(homes, tmp_path, monkeypatch):
    from agent_friday.services import friday_bundle as fb
    a, b, _ = homes
    out = tmp_path / "x.fridaybundle"
    _make(a, out)
    b.mkdir(parents=True)
    lock = b / "friday_server.lock"
    fh = open(lock, "a+b")
    fh.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        _point_at(b, monkeypatch)
        with pytest.raises(fb.BundleError, match="running"):
            fb.restore(out, PASS, home=b, replace=True)
    finally:
        fh.close()


def test_what_a_fresh_install_left_is_moved_aside_not_merged(homes, tmp_path, monkeypatch):
    from agent_friday.services import friday_bundle as fb
    a, b, _ = homes
    out = tmp_path / "x.fridaybundle"
    _make(a, out)
    (b / "skills" / "bundled").mkdir(parents=True)
    (b / "settings.json").write_text('{"seeded": true}', encoding="utf-8")
    _point_at(b, monkeypatch)
    r = fb.restore(out, PASS, home=b)
    assert r["moved_aside"] and (b.parent / r["moved_aside"] / "settings.json").exists()
    assert json.loads((b / "settings.json").read_text(encoding="utf-8")) == {"agent_name": "Friday"}
    assert not (b / "skills" / "bundled").exists()


# ── security review round 1 ─────────────────────────────────────────────────

class _FakeKeyring:
    """A keychain for one Windows account, in memory."""

    class errors:
        class NoKeyringError(Exception):
            pass

    def __init__(self):
        self.store = {}

    def get_password(self, service, account):
        return self.store.get((service, account))

    def set_password(self, service, account, value):
        self.store[(service, account)] = value

    def delete_password(self, service, account):
        self.store.pop((service, account), None)


def test_connector_secrets_in_the_mcp_list_and_the_signing_certificate_do_not_travel(homes, tmp_path, monkeypatch):
    from agent_friday.services import friday_bundle as fb
    a, b, _ = homes
    (a / "mcp_servers.json").write_text(json.dumps({"servers": {"github": {
        "command": "npx", "args": ["server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_placeholdernotreal", "GITHUB_ORG": "octo"}}}}),
        encoding="utf-8")   # pragma: allowlist secret
    (a / "signing").mkdir()
    (a / "signing" / "certificate_p12.bin").write_bytes(b"certificate placeholder")
    (a / "signing" / "certificate_passphrase.bin").write_bytes(b"passphrase placeholder")
    out = tmp_path / "x.fridaybundle"
    _make(a, out)
    _point_at(b, monkeypatch)
    r = fb.restore(out, PASS, home=b)
    cfg = json.loads((b / "mcp_servers.json").read_text(encoding="utf-8"))
    env = cfg["servers"]["github"]["env"]
    assert env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "" and env["GITHUB_ORG"] == "octo"
    assert "ghp_placeholdernotreal" not in out.read_bytes().decode("latin-1")
    assert r["reconnect"]["mcp_servers_needing_their_keys"] == ["github"]
    assert not (b / "signing").exists()


def test_a_restore_never_loses_the_governance_key_this_account_already_holds(homes, tmp_path, monkeypatch):
    from agent_friday.services import friday_bundle as fb
    a, b, _ = homes
    out = tmp_path / "x.fridaybundle"
    _make(a, out)
    kr = _FakeKeyring()
    other = "11" * 32
    kr.set_password("agent-friday", "governance-key", other)    # another Friday on this account
    monkeypatch.setitem(sys.modules, "keyring", kr)
    monkeypatch.setitem(sys.modules, "keyring.errors", kr.errors)
    _point_at(b, monkeypatch)
    with pytest.raises(fb.BundleError, match="governance key"):
        fb.restore(out, PASS, home=b)
    assert kr.get_password("agent-friday", "governance-key") == other
    r = fb.restore(out, PASS, home=b, replace=True)
    kept = r["keys"]["kept_previous_key"]
    assert kr.get_password("agent-friday", kept) == other
    assert kr.get_password("agent-friday", "governance-key") == (a / "vault" / ".governance-key").read_bytes().hex()


def test_the_owners_own_files_keep_their_names_and_friday_keys_its_own(homes, tmp_path, monkeypatch):
    from agent_friday.services import friday_bundle as fb
    a, b, _ = homes
    for rel in ("codebases/app/poetry.lock", "workspaces/ws1/talk.key", "library/owner/index.key"):
        (a / rel).parent.mkdir(parents=True, exist_ok=True)
        (a / rel).write_bytes(b"owner content " + rel.encode())
    out = tmp_path / "x.fridaybundle"
    _make(a, out)
    _point_at(b, monkeypatch)
    fb.restore(out, PASS, home=b)
    for rel in ("codebases/app/poetry.lock", "workspaces/ws1/talk.key", "library/owner/index.key"):
        assert (b / rel).read_bytes() == (a / rel).read_bytes(), rel


def test_names_are_judged_without_case():
    from pathlib import PurePosixPath as P
    from agent_friday.services import friday_bundle as fb
    for rel in ("Security/keystore2.json", "VAULT/.governance-key", "Providers/keys/x.key",
                "RESTORE/hold.json", "Signing/certificate_p12.bin"):
        assert fb.category_of(P(rel)) is None, rel


def test_a_header_asking_for_absurd_key_settings_is_refused_cleanly(homes, tmp_path):
    import base64
    from agent_friday.services import friday_bundle as fb
    header = {"format": fb.FORMAT, "bundle_id": "fb-x", "nonce_prefix": base64.b64encode(b"8bytes!!").decode(),
              "kdf": {"name": "argon2id", "t": 1, "m": 2 ** 32 - 1, "p": 1,
                      "salt": base64.b64encode(os.urandom(16)).decode()}}
    raw = json.dumps(header).encode()
    out = tmp_path / "bad.fridaybundle"
    out.write_bytes(fb.MAGIC + struct.pack(">I", len(raw)) + raw + b"\x00" * 64)
    with pytest.raises(fb.BundleError, match="key settings"):
        fb.verify(out, PASS)


# ── security review round 2 ─────────────────────────────────────────────────

def test_old_sign_ins_client_secrets_browser_profiles_and_grants_stay_behind(homes, tmp_path, monkeypatch):
    from agent_friday.services import friday_bundle as fb
    a, b, _ = homes
    stay = {"google_token.json": '{"refresh_token": "placeholder"}',
            "credentials.json": "{}", "oauth-keys.json": "{}", ".env": "X=placeholder",
            "client_secret_123.json": "{}", "browser-sessions/t1/Cookies": "placeholder",
            "governance/desktop_app_grants.json": "{}", "cc_permission": "granted"}
    for rel, text in stay.items():
        (a / rel).parent.mkdir(parents=True, exist_ok=True)
        (a / rel).write_text(text, encoding="utf-8")
    d = json.loads((a / "settings.json").read_text(encoding="utf-8"))
    d.update(anthropic_api_key="placeholder-old-key", max_tokens=4096,  # pragma: allowlist secret
             telegram_bot_token="placeholder", voice_session_mode="push")
    (a / "settings.json").write_text(json.dumps(d), encoding="utf-8")
    (a / "mcp_servers.json").write_text(json.dumps({"servers": {"db": {
        "command": "srv", "args": ["--api-key", "placeholder-arg-key", "--verbose"],
        "url": "https://example.test/mcp?api_key=placeholder",
        "env": {"DATABASE_URL": "postgres://user:placeholderpw@db.example.test/x", "OPENAI_KEY": "p",
                "REGION": "eu"}}}}), encoding="utf-8")
    out = tmp_path / "x.fridaybundle"
    _make(a, out)
    _point_at(b, monkeypatch)
    r = fb.restore(out, PASS, home=b)
    for rel in stay:
        assert not (b / rel).exists(), rel
    s = json.loads((b / "settings.json").read_text(encoding="utf-8"))
    assert s["anthropic_api_key"] == "" and s["telegram_bot_token"] == ""
    assert s["max_tokens"] == 4096 and s["voice_session_mode"] == "push"
    srv = json.loads((b / "mcp_servers.json").read_text(encoding="utf-8"))["servers"]["db"]
    assert srv["args"] == ["--api-key", "", "--verbose"]
    assert srv["url"] == "" and srv["env"]["DATABASE_URL"] == "" and srv["env"]["OPENAI_KEY"] == ""
    assert srv["env"]["REGION"] == "eu"
    assert r["reconnect"]["mcp_servers_needing_their_keys"] == ["db"]


def test_a_keychain_that_does_not_answer_is_never_written_over(homes, tmp_path, monkeypatch):
    from agent_friday.services import friday_bundle as fb
    a, b, _ = homes
    out = tmp_path / "x.fridaybundle"
    _make(a, out)

    class Broken(_FakeKeyring):
        def get_password(self, service, account):
            raise RuntimeError("keychain did not answer")
    kr = Broken()
    monkeypatch.setitem(sys.modules, "keyring", kr)
    monkeypatch.setitem(sys.modules, "keyring.errors", kr.errors)
    _point_at(b, monkeypatch)
    with pytest.raises(fb.BundleError, match="keychain"):
        fb.restore(out, PASS, home=b, replace=True)
    assert kr.store == {} and not b.exists()


def test_outward_actions_are_held_even_when_a_late_restore_step_fails(homes, tmp_path, monkeypatch):
    from agent_friday.services import friday_bundle as fb
    a, b, _ = homes
    out = tmp_path / "x.fridaybundle"
    _make(a, out)
    _point_at(b, monkeypatch)
    monkeypatch.setattr(fb, "_install_keys", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        fb.restore(out, PASS, home=b)
    assert (b / "restore" / "hold.json").exists()
