"""One vault, one passphrase: a stray keychain value must not split it or hide it.

The OS keychain slot agent-friday/vault-passphrase is one per account, while
the protected DPAPI file and the launcher belong to one Friday home. When they
hold different passphrases, only one of them may open the sealed files (the
wiki, the vault folders, the knowledge graph's private records). These pin:

  * resolve() uses the passphrase that opens the most of a sample of sealed
    files across the home, not precedence alone;
  * a scratch home (tests, rehearsals) never reads or writes the account's
    keychain, and a real home named by FRIDAY_HOME still does;
  * store() never silently replaces the passphrase that opens the vault, never
    writes one that opens none of it, and a change is a rotation that re-seals
    every file first, offline only, and aborts if the keystore cannot follow;
  * the knowledge graph reads a file sealed under an older key, never replaces
    or removes one it cannot open or could not keep, rebuilds its key cache on
    a passphrase change, and says what it found (/api/health, one card);
  * scripts/vault_key_doctor.py refuses to write beside a live Friday and keeps
    the keychain's old value, read back, before it replaces it.

Argon2id runs with the FAST test profile here; production derivation is
unchanged. Nothing under the real ~/.friday or the real keychain is touched.
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))


class _FakeKeyring:
    def __init__(self):
        self.store = {}

    def get_password(self, service, account):
        return self.store.get((service, account))

    def set_password(self, service, account, value):
        self.store[(service, account)] = value


def _install_keyring(monkeypatch, fk, real=False):
    mod = types.ModuleType("keyring")
    mod.get_password = fk.get_password
    mod.set_password = fk.set_password
    if real:                  # the real package, with an OS-backed backend
        backend = type("WinVaultKeyring", (), {"__module__": "keyring.backends.Windows"})
        mod.get_keyring = lambda: backend()
    monkeypatch.setitem(sys.modules, "keyring", mod)


@pytest.fixture
def env(tmp_path, monkeypatch):
    import agent_friday.core as core
    from agent_friday.privacy import vault_crypto as vc
    from agent_friday.services import vault_passphrase as vp

    real_derive = vc.derive_key
    monkeypatch.setattr(vc, "derive_key",
                        lambda p, s, profile=None: real_derive(p, s, vc.FAST_PROFILE))
    home = tmp_path / "profile" / ".friday"
    (home / "vault").mkdir(parents=True)
    salt = os.urandom(16)
    (home / "vault" / ".vault_config.json").write_text(
        json.dumps({"salt_hex": salt.hex()}), encoding="utf-8")
    monkeypatch.setattr(core, "FRIDAY_DIR", home)
    monkeypatch.setattr(vp, "_SECURITY_DIR", home / "security")
    monkeypatch.setattr(core, "ENV_FROM_LAUNCH_SCRIPTS", set(), raising=False)
    for var in ("FRIDAY_VAULT_PASSPHRASE", "FRIDAY_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(vp, "_from_dpapi_file", lambda: "")
    monkeypatch.setattr(vp, "_from_start_bat", lambda: "")
    # A reversible stand-in for DPAPI, so kept values and the protected file
    # behave the same on every OS and never touch the real DPAPI.
    monkeypatch.setattr(vp, "dpapi", lambda data, encrypt: _fake_dpapi(data, encrypt))
    monkeypatch.setattr(vp, "dpapi_available", lambda: True)
    vp.reset_cache()
    fk = _FakeKeyring()
    _install_keyring(monkeypatch, fk)

    def seal(rel: str, data: bytes, passphrase: str) -> Path:
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(vc.encrypt(data, vc.derive_key(passphrase, salt)))
        return p

    def launcher(value: str):
        monkeypatch.setenv("FRIDAY_PASSWORD", value)
        monkeypatch.setattr(core, "ENV_FROM_LAUNCH_SCRIPTS", {"FRIDAY_PASSWORD"}, raising=False)
        vp.reset_cache()

    ns = types.SimpleNamespace(home=home, salt=salt, vc=vc, vp=vp, core=core, keyring=fk,
                               seal=seal, launcher=launcher, monkeypatch=monkeypatch)
    yield ns
    vp.reset_cache()


# ── which passphrase ────────────────────────────────────────────────────────

def test_the_passphrase_that_opens_the_data_beats_a_stray_keychain_value(env):
    env.seal("vault/family/note.vault", b"private", "P2-opens-everything")
    env.keyring.set_password("agent-friday", "vault-passphrase", "P1-stray")
    env.launcher("P2-opens-everything")
    value, source = env.vp.resolve()
    assert value == "P2-opens-everything", source
    c = env.vp.LAST_CONFLICT
    assert c["precedence_would_pick"] == "your operating system's keychain"
    assert "your operating system's keychain" in c["opens_nothing"]


def test_agreeing_homes_cost_no_check_and_keep_precedence(env, monkeypatch):
    env.seal("vault/x.vault", b"x", "same")
    env.keyring.set_password("agent-friday", "vault-passphrase", "same")
    env.launcher("same")
    monkeypatch.setattr(env.vp, "verify", lambda v: pytest.fail("verify ran"))
    assert env.vp.resolve() == ("same", "your operating system's keychain")


def test_the_disagreement_is_said_out_loud(env, monkeypatch):
    env.seal("vault/family/note.vault", b"private", "P2")
    env.keyring.set_password("agent-friday", "vault-passphrase", "P1")
    env.launcher("P2")
    monkeypatch.setattr(env.vp, "_SCAN", {"done": True, "unreadable": 0})
    env.vp.resolve()                       # the probe reports a resolved conflict only
    out = env.vp._integrity_probe()
    assert out and out[0]["state"] == "passphrase_homes_disagree"
    assert "opens none of them" in out[0]["message"]
    assert "P1" not in out[0]["message"] and "P2" not in out[0]["message"]


# ── the keychain is per Windows account, not per Friday home ────────────────

def test_a_redirected_home_never_writes_the_real_keychain(env):
    fk = _FakeKeyring()
    _install_keyring(env.monkeypatch, fk, real=True)
    env.monkeypatch.setattr(env.vp, "_real_profile_dir", lambda: env.home.parent.parent / "owner")
    env.vp.store("rehearsal-passphrase")
    assert fk.store == {}, "a scratch home wrote the account's real keychain"


def test_a_redirected_home_does_not_read_the_owners_keychain(env):
    fk = _FakeKeyring()
    fk.set_password("agent-friday", "vault-passphrase", "the-owners")
    _install_keyring(env.monkeypatch, fk, real=True)
    env.monkeypatch.setattr(env.vp, "_real_profile_dir", lambda: env.home.parent.parent / "owner")
    env.vp.reset_cache()
    assert env.vp.resolve()[0] != "the-owners"


def _not_scratch(env, account_dir):
    """The process looks like an ordinary logon: no FRIDAY_TESTING, profile not
    redirected, home not under a temp directory."""
    mp = env.monkeypatch
    mp.delenv("FRIDAY_TESTING", raising=False)
    mp.setattr(env.vp, "_real_profile_dir", lambda: account_dir, raising=False)
    mp.setattr(env.vp, "_process_profile_dir", lambda: account_dir, raising=False)
    mp.setattr(env.vp, "_temp_roots", lambda: [account_dir / "no-temp-here"], raising=False)


def test_the_accounts_own_home_still_uses_the_keychain(env):
    fk = _FakeKeyring()
    _install_keyring(env.monkeypatch, fk, real=True)
    _not_scratch(env, env.home.parent)
    assert "keychain" in env.vp.store("the-owners")
    assert fk.get_password("agent-friday", "vault-passphrase") == "the-owners"


# ── store() never silently replaces the working passphrase ─────────────────

def test_store_refuses_to_replace_the_passphrase_that_opens_the_vault(env):
    env.seal("vault/legal/a.vault", b"a", "A")
    env.keyring.set_password("agent-friday", "vault-passphrase", "A")
    assert env.vp.store("B") == []
    assert env.keyring.get_password("agent-friday", "vault-passphrase") == "A"
    assert "re-encrypt" in env.vp.LAST_STORE_REFUSAL


def test_a_deliberate_change_reseals_every_file_before_switching(env):
    files = [env.seal("vault/legal/a.vault", b"a", "A"),
             env.seal("wiki/entities/p.md", b"page", "A"),
             env.seal("knowledge-graph/entities.sensitive.json", b"[{}]", "A")]
    env.keyring.set_password("agent-friday", "vault-passphrase", "A")
    written = env.vp.store("B", rotate=True)
    assert "keychain" in written
    kb = env.vc.derive_key("B", env.salt)
    for f in files:
        env.vc.decrypt(f.read_bytes(), kb)                 # opens under B
    backups = list((env.home / "security").glob("pre-rotation-*/**/*.vault"))
    assert backups, "the originals were not kept"


def test_first_run_setup_does_not_arm_a_refused_passphrase(env):
    env.seal("vault/legal/a.vault", b"a", "A")
    env.keyring.set_password("agent-friday", "vault-passphrase", "A")
    from agent_friday.services import setup_complete
    assert setup_complete.store_vault_passphrase("B") is False
    assert os.environ.get("FRIDAY_VAULT_PASSPHRASE") != "B"


# ── the knowledge graph's sealed files ─────────────────────────────────────

@pytest.fixture
def kg(env, monkeypatch):
    from agent_friday.services.knowledge_graph import store as st
    kgdir = env.home / "knowledge-graph"
    kgdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(st, "KG_DIR", kgdir)
    k_current = env.vc.derive_key("P1-current", env.salt)
    k_old = env.vc.derive_key("P2-older", env.salt)
    monkeypatch.setattr(st, "_vault_key", lambda: k_current)
    for name in ("_KEYS_CACHE", "SENSITIVE_STATE", "_SAID", "LOAD_VERDICT"):
        if hasattr(st, name):                      # absent before this fix
            getattr(st, name).clear()
    ns = types.SimpleNamespace(st=st, dir=kgdir, k_current=k_current, k_old=k_old)

    def legacy(keys):
        from agent_friday.services import credential_store as cs
        monkeypatch.setattr(cs, "_legacy_keys", lambda: keys)
        if hasattr(st, "_KEYS_CACHE"):
            st._KEYS_CACHE.clear()
    ns.legacy = legacy
    return ns


def _sealed_entities(kg, key, n=3):
    recs = [{"id": "e%d" % i, "title": "t", "provenance": {"sensitivity": 3}} for i in range(n)]
    from agent_friday.privacy import vault_crypto as vc
    p = kg.dir / "entities.sensitive.json"
    p.write_bytes(vc.encrypt(json.dumps(recs).encode("utf-8"), key))
    return p, recs


def test_a_sealed_file_under_an_older_key_is_still_read(kg):
    _sealed_entities(kg, kg.k_old, 4)
    kg.legacy([("launcher:FRIDAY_PASSWORD", kg.k_old)])
    recs = kg.st.KnowledgeGraphStore(kg.dir).load("entities")
    assert len(recs) == 4
    state = kg.st.SENSITIVE_STATE[str(kg.dir / "entities.sensitive.json")]
    assert state["state"] == "older_key" and state["records"] == 4


def test_save_never_destroys_a_sealed_file_no_key_opens(kg):
    p, _ = _sealed_entities(kg, kg.k_old, 5)
    before = p.read_bytes()
    kg.legacy([])                                  # nothing Friday knows opens it
    store = kg.st.KnowledgeGraphStore(kg.dir)
    assert store.load("entities") == []            # it cannot read them...
    out = store.save("entities", [{"id": "pub", "provenance": {"sensitivity": 1}}])
    assert out.get("refused") in ("stale_load", "cannot_decrypt")
    assert p.read_bytes() == before, "the unreadable sealed file was replaced or removed"


def test_clear_leaves_a_sealed_file_no_key_opens_in_place(kg):
    p, _ = _sealed_entities(kg, kg.k_old, 2)
    before = p.read_bytes()
    kg.legacy([])
    kg.st.KnowledgeGraphStore(kg.dir).clear()
    assert p.exists() and p.read_bytes() == before


def test_save_still_replaces_a_file_the_current_key_opens(kg):
    _sealed_entities(kg, kg.k_current, 2)
    store = kg.st.KnowledgeGraphStore(kg.dir)
    store.save("entities", [{"id": "x", "provenance": {"sensitivity": 3}}])
    assert not list(kg.dir.glob("*.kept-*.json"))
    assert [r["id"] for r in store.load("entities")] == ["x"]


def test_an_unreadable_sealed_file_reaches_health_and_one_card(kg, monkeypatch):
    _sealed_entities(kg, kg.k_old, 2)
    kg.legacy([])
    kg.st.KnowledgeGraphStore(kg.dir).load("entities")   # what load saw is what health says
    from agent_friday.services import integrity_report as ir
    monkeypatch.setattr(ir, "_ANNOUNCED", set())
    monkeypatch.setattr(ir, "_PROBES", {"knowledge graph": kg.st._integrity_probe})
    field = ir.health_field()
    assert field["ok"] is False
    assert field["problems"][0]["state"] == "cannot_decrypt"
    assert "missing from the graph" in field["problems"][0]["message"]
    pushed = []
    from agent_friday import notifications_engine
    monkeypatch.setattr(notifications_engine, "push", lambda **kw: pushed.append(kw))
    ir.announce()
    ir.announce()
    assert [k["dedupe_key"] for k in pushed if k.get("emitter") == "DI1"] == \
        ["data-integrity:knowledge graph"]


# ── C1: the knowledge graph's key cache and an unreadable sealed file ──────

def test_the_kg_key_cache_is_rebuilt_when_a_passphrase_is_stored(kg, env, monkeypatch):
    _sealed_entities(kg, kg.k_old, 4)
    kg.legacy([])
    store = kg.st.KnowledgeGraphStore(kg.dir)
    assert store.load("entities") == []            # cache now built with no older key
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(cs, "_legacy_keys", lambda: [("keychain", kg.k_old)])
    # store() is the key change; nothing here clears the graph's cache by hand.
    assert env.vp.store("P2-older")
    assert len(store.load("entities")) == 4, "the key cache outlived a passphrase store"


def test_a_save_is_refused_while_the_sealed_file_cannot_be_decrypted(kg):
    p, _ = _sealed_entities(kg, kg.k_old, 3)
    sealed_before = p.read_bytes()
    plain = kg.dir / "entities.json"
    plain.write_text('[{"id": "old-public"}]', encoding="utf-8")
    plain_before = plain.read_bytes()
    kg.legacy([])
    store = kg.st.KnowledgeGraphStore(kg.dir)
    store.load("entities")
    assert kg.st.SENSITIVE_STATE[str(p)]["state"] == "cannot_decrypt"

    out = store.save("entities", [{"id": "pub", "provenance": {"sensitivity": 1}},
                                  {"id": "sec", "provenance": {"sensitivity": 3}}])
    assert out.get("refused") in ("stale_load", "cannot_decrypt")
    assert p.read_bytes() == sealed_before and plain.read_bytes() == plain_before
    store.save("entities", [{"id": "pub", "provenance": {"sensitivity": 1}}])
    assert p.exists() and p.read_bytes() == sealed_before, "the sealed file was unlinked"
    assert not list(kg.dir.glob("*.kept-*.json"))


# ── C3: a kept copy that cannot be made stops the save or the clear ────────

def test_a_failed_keep_aborts_the_save_and_the_clear(kg, monkeypatch):
    p, _ = _sealed_entities(kg, kg.k_old, 2)
    kg.legacy([("launcher:FRIDAY_PASSWORD", kg.k_old)])   # only an OLDER key opens it
    sealed_before = p.read_bytes()
    plain = kg.dir / "entities.json"
    plain.write_text("[]", encoding="utf-8")
    layout = kg.dir / "layout.json"
    layout.write_text("{}", encoding="utf-8")

    real_write = Path.write_bytes

    def failing_write(self, data):
        if ".kept-" in self.name:
            raise OSError("disk full")
        return real_write(self, data)
    monkeypatch.setattr(Path, "write_bytes", failing_write)

    store = kg.st.KnowledgeGraphStore(kg.dir)
    out = store.save("entities", [{"id": "x", "provenance": {"sensitivity": 3}}])
    assert out.get("refused") == "keep_failed"
    assert p.read_bytes() == sealed_before, "replaced although its copy was never kept"
    assert plain.read_text(encoding="utf-8") == "[]"

    store.clear()
    assert p.exists() and p.read_bytes() == sealed_before, "removed although never kept"
    assert plain.exists() and layout.exists(), "clear went ahead after a failed keep"


# ── C2: verification samples the whole home; store() never fails open ─────

def test_the_passphrase_that_opens_most_of_the_home_wins(env):
    # The vault folder's only file is sealed under A; the rest of the home
    # (five wiki pages) under B. A check of the vault folder alone picks A.
    env.seal("vault/a.vault", b"a", "A-one-file")
    for i in range(5):
        env.seal("wiki/entities/p%d.md" % i, b"page", "B-most-files")
    env.keyring.set_password("agent-friday", "vault-passphrase", "A-one-file")
    env.launcher("B-most-files")
    assert env.vp.resolve()[0] == "B-most-files"
    assert env.vp._working_passphrase() == "B-most-files"


def test_store_refuses_when_sealed_files_exist_and_nothing_opens_them(env):
    env.seal("vault/legal/a.vault", b"a", "REAL")
    env.seal("wiki/entities/p.md", b"p", "REAL")
    env.keyring.set_password("agent-friday", "vault-passphrase", "STRAY")
    assert env.vp.store("ALSO-WRONG") == []
    assert env.keyring.get_password("agent-friday", "vault-passphrase") == "STRAY"
    assert env.vp.LAST_STORE_REFUSAL
    # The passphrase that does open the files is still accepted.
    assert "keychain" in env.vp.store("REAL")
    assert env.keyring.get_password("agent-friday", "vault-passphrase") == "REAL"


# ── C4: no rotation from the live route; a failed keystore rewrap aborts ──

def test_the_live_route_refuses_to_rotate(env):
    import flask
    from agent_friday.routes import insights as ins
    f = env.seal("vault/legal/a.vault", b"a", "A-old-pass")
    env.keyring.set_password("agent-friday", "vault-passphrase", "A-old-pass")
    app = flask.Flask("route-test")
    with app.test_request_context("/api/vault/passphrase", method="POST",
                                  json={"passphrase": "B-new-pass"}):
        resp, status = ins.set_vault_passphrase.__wrapped__()
    assert status == 409
    assert "vault_key_doctor.py --rotate" in resp.get_json()["message"]
    env.vc.decrypt(f.read_bytes(), env.vc.derive_key("A-old-pass", env.salt))
    assert env.keyring.get_password("agent-friday", "vault-passphrase") == "A-old-pass"
    assert os.environ.get("FRIDAY_VAULT_PASSPHRASE") != "B-new-pass"


def test_a_failed_keystore_rewrap_aborts_the_rotation(env, monkeypatch):
    files = [env.seal("vault/legal/a.vault", b"a", "A"),
             env.seal("wiki/entities/p.md", b"page", "A")]
    env.keyring.set_password("agent-friday", "vault-passphrase", "A")
    monkeypatch.setattr(env.vp, "_keystore_state", lambda: ("ok", "A"))
    monkeypatch.setattr(env.vp, "_rewrap_keystore", lambda p: False)
    assert env.vp.store("B", rotate=True) == []
    assert "keystore" in env.vp.LAST_STORE_REFUSAL
    ka = env.vc.derive_key("A", env.salt)
    for f in files:
        env.vc.decrypt(f.read_bytes(), ka)                 # still sealed under A
    assert env.keyring.get_password("agent-friday", "vault-passphrase") == "A"


# ── a real FRIDAY_HOME is not a scratch home ──────────────────────────────

def test_a_real_friday_home_keeps_the_keychain(env, tmp_path):
    fk = _FakeKeyring()
    _install_keyring(env.monkeypatch, fk, real=True)
    _not_scratch(env, tmp_path / "account")              # the home is elsewhere
    env.monkeypatch.setenv("FRIDAY_HOME", str(env.home))
    assert "keychain" in env.vp.store("owner-pass")
    assert fk.get_password("agent-friday", "vault-passphrase") == "owner-pass"
    # The same home under a test run is scratch again.
    env.monkeypatch.setenv("FRIDAY_TESTING", "1")
    env.vp.reset_cache()
    assert env.vp.keychain_is_this_homes() is False


# ── the doctor ─────────────────────────────────────────────────────────────

def _doctor():
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "scripts" / "vault_key_doctor.py"
    spec = importlib.util.spec_from_file_location("vault_key_doctor_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_dpapi(data, encrypt):
    return (b"W" + data[::-1]) if encrypt else data[1:][::-1]


def test_the_doctor_refuses_while_a_server_holds_this_homes_lock(env, monkeypatch):
    doc = _doctor()
    monkeypatch.setenv("FRIDAY_HOME", str(env.home))
    monkeypatch.setattr(doc, "_tray_running", lambda: False)
    monkeypatch.setattr(doc, "_ports", lambda extra: [])
    held = doc.take_server_lock()                         # stands in for a live server
    assert held is not None
    try:
        reasons = doc.running_reasons(None)
        assert reasons and "friday_server.lock" in reasons[0]
        assert doc.main(["--repair-keychain"]) == 2
    finally:
        doc.release_server_lock(held)
    assert doc.running_reasons(None) == []


def test_the_doctor_keeps_each_stray_value_under_its_own_name(env, monkeypatch):
    doc = _doctor()
    monkeypatch.setattr(env.vp, "dpapi", _fake_dpapi)
    env.seal("vault/family/note.vault", b"private", "P2")
    env.launcher("P2")
    env.keyring.set_password("agent-friday", "vault-passphrase", "P1-stray")
    out = doc.repair_keychain()
    assert out["ok"], out
    env.keyring.set_password("agent-friday", "vault-passphrase", "P0-stray")
    env.vp.reset_cache()
    assert doc.repair_keychain()["ok"]
    kept = sorted((env.home / "security").glob("stray-*.dpapi"))
    values = sorted(_fake_dpapi(k.read_bytes()[len(env.vp.DPAPI_MAGIC):], False).decode()
                    for k in kept)
    assert values == ["P0-stray", "P1-stray"], "an earlier kept value was overwritten"
    assert env.keyring.get_password("agent-friday", "vault-passphrase") == "P2"


def test_the_doctor_leaves_the_keychain_alone_when_the_kept_copy_does_not_read_back(env, monkeypatch):
    doc = _doctor()
    monkeypatch.setattr(env.vp, "dpapi",
                        lambda data, encrypt: (b"W" + data) if encrypt else b"garbage")
    env.seal("vault/family/note.vault", b"private", "P2")
    env.launcher("P2")
    env.keyring.set_password("agent-friday", "vault-passphrase", "P1-stray")
    out = doc.repair_keychain()
    assert out["ok"] is False
    assert env.keyring.get_password("agent-friday", "vault-passphrase") == "P1-stray"


# ── F1: a home's different value is kept before store() overwrites it ─────

def _kept(env):
    out = []
    for k in sorted((env.home / "security").glob("stray-*.dpapi")):
        out.append(_fake_dpapi(k.read_bytes()[len(env.vp.DPAPI_MAGIC):], False).decode())
    return out


def test_store_keeps_a_homes_different_value_before_overwriting_it(env):
    env.seal("vault/legal/a.vault", b"a", "P2")
    env.keyring.set_password("agent-friday", "vault-passphrase", "P1-still-opens-something")
    assert "keychain" in env.vp.store("P2")
    assert env.keyring.get_password("agent-friday", "vault-passphrase") == "P2"
    assert _kept(env) == ["P1-still-opens-something"], "the replaced value was lost"
    assert ("P1-still-opens-something" in
            [v for _, v in env.vp.kept_values()])


def test_store_refuses_when_the_value_it_would_replace_cannot_be_kept(env, monkeypatch):
    env.seal("vault/legal/a.vault", b"a", "P2")
    env.keyring.set_password("agent-friday", "vault-passphrase", "P1-old")
    monkeypatch.setattr(env.vp, "dpapi",
                        lambda data, encrypt: (b"W" + data) if encrypt else b"garbage")
    assert env.vp.store("P2") == []
    assert env.keyring.get_password("agent-friday", "vault-passphrase") == "P1-old"
    assert "nothing was written" in env.vp.LAST_STORE_REFUSAL


def test_a_kept_value_is_a_read_key_for_the_graph(kg, env, monkeypatch):
    _sealed_entities(kg, kg.k_old, 3)                      # sealed under "P2-older"
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(cs, "_VAULT_CONFIG_FILE", env.home / "vault" / ".vault_config.json")
    env.vp.keep_displaced("P2-older")                      # held by no home any more
    kg.st._KEYS_CACHE.clear()
    assert len(kg.st.KnowledgeGraphStore(kg.dir).load("entities")) == 3


# ── F2: the keystore opens with whichever known passphrase wraps it ────────

@pytest.fixture
def keystore(env, monkeypatch, tmp_path):
    from agent_friday.services import keystore as ks
    monkeypatch.setattr(ks, "KEYSTORE_PATH", tmp_path / "ks" / "keystore.json")
    monkeypatch.setattr(ks, "_derive", lambda pw, salt: env.vc.derive_key(pw, salt))
    monkeypatch.setattr(ks, "_CACHED_KEY", None)
    yield ks
    ks._reset_cache_for_tests()


def test_the_keystore_unwraps_with_the_passphrase_that_wraps_it(env, keystore):
    keystore.create(wrap="passphrase", passphrase="P1-wraps-the-keystore")
    keystore._reset_cache_for_tests()
    env.seal("vault/legal/a.vault", b"a", "P2-opens-the-files")
    env.keyring.set_password("agent-friday", "vault-passphrase", "P1-wraps-the-keystore")
    env.launcher("P2-opens-the-files")
    assert env.vp.resolve()[0] == "P2-opens-the-files"
    assert len(keystore.root_key()) == 32, "provider keys locked by the other passphrase"
    assert keystore.unwrapping_passphrase() == "P1-wraps-the-keystore"


def test_store_refuses_while_the_keystore_is_wrapped_and_nothing_unlocks_it(env, keystore):
    keystore.create(wrap="passphrase", passphrase="known-to-nothing-here")
    keystore._reset_cache_for_tests()
    env.seal("vault/legal/a.vault", b"a", "A")
    assert env.vp.store("A") == []
    assert "keystore" in env.vp.LAST_STORE_REFUSAL
    assert env.keyring.get_password("agent-friday", "vault-passphrase") is None


# ── F3: recovery merges, never replaces newer data, and retires kept copies ─

def test_recovery_never_merges_kept_copies_into_a_live_file_and_retires_them(kg, env, monkeypatch):
    """Records missing from a live file the current key opens may have been
    removed on purpose (a deleted document, a forget); a kept copy must not
    bring them back."""
    from agent_friday.privacy import vault_crypto as vc
    import agent_friday.services.agent as agent
    monkeypatch.setattr(agent, "_get_vault_key", lambda: kg.k_current)
    live = kg.dir / "entities.sensitive.json"
    live.write_bytes(vc.encrypt(json.dumps(
        [{"id": "new1"}, {"id": "shared", "v": "new"}]).encode(), kg.k_current))
    for stamp, rid in (("20261001T000000", "removed-on-purpose"), ("20261002T000000", "old2")):
        (kg.dir / ("entities.sensitive.kept-%s.json" % stamp)).write_bytes(vc.encrypt(
            json.dumps([{"id": rid}, {"id": "shared", "v": "old"}]).encode(), kg.k_old))
    kg.legacy([("launcher:FRIDAY_PASSWORD", kg.k_old)])
    doc = _doctor()
    first = doc.recover_kg()
    assert first["ok"], first
    recs = json.loads(vc.decrypt(live.read_bytes(), kg.k_current))
    assert {r["id"] for r in recs} == {"new1", "shared"}, "a removed record came back"
    assert [r for r in recs if r["id"] == "shared"][0]["v"] == "new"
    assert [x["records_not_merged"] for x in first["retired_unmerged"]] == [2, 2]
    assert not list(kg.dir.glob("*.kept-*.json")), "a kept copy was not retired"
    backups = [b for b in (env.home / "security").glob("kg-pre-recover-*/*") if b.is_file()]
    assert len(backups) == 2
    again = doc.recover_kg()
    assert again["ok"] and again["resealed"] == [] and again["retired_unmerged"] == []


def test_recovery_restores_a_dropped_copy_whose_live_file_is_absent(kg, env, monkeypatch):
    from agent_friday.privacy import vault_crypto as vc
    import agent_friday.services.agent as agent
    monkeypatch.setattr(agent, "_get_vault_key", lambda: kg.k_current)
    (kg.dir / "entities.sensitive.dropped-20261001T000000.json").write_bytes(
        vc.encrypt(json.dumps([{"id": "old1"}]).encode(), kg.k_old))
    kg.legacy([("launcher:FRIDAY_PASSWORD", kg.k_old)])
    out = _doctor().recover_kg()
    assert out["ok"] and out["resealed"][0]["records"] == 1
    live = kg.dir / "entities.sensitive.json"
    assert json.loads(vc.decrypt(live.read_bytes(), kg.k_current)) == [{"id": "old1"}]
    assert not list(kg.dir.glob("*.dropped-*.json"))


def test_backups_of_one_name_never_overwrite_each_other(tmp_path):
    doc = _doctor()
    src = tmp_path / "entities.sensitive.json"
    bdir = tmp_path / "b"
    src.write_bytes(b"one")
    doc._backup(src, bdir)
    src.write_bytes(b"two")
    doc._backup(src, bdir)
    assert sorted(f.read_bytes() for f in bdir.iterdir()) == [b"one", b"two"]


# ── F4: a save after an unreadable load waits for a fresh load ─────────────

def test_a_save_after_an_unreadable_load_is_refused_even_once_the_key_is_known(kg):
    p, _ = _sealed_entities(kg, kg.k_old, 3)
    before = p.read_bytes()
    kg.legacy([])
    store = kg.st.KnowledgeGraphStore(kg.dir)
    assert store.load("entities") == []
    kg.legacy([("launcher:FRIDAY_PASSWORD", kg.k_old)])   # the key is found later
    out = store.save("entities", [{"id": "x", "provenance": {"sensitivity": 3}}])
    assert out.get("refused") == "stale_load"
    assert p.read_bytes() == before, "replaced records its caller never saw"
    assert len(store.load("entities")) == 3                # a fresh load lifts it
    assert "refused" not in store.save("entities", [{"id": "x", "provenance": {"sensitivity": 3}}])


# ── F5: a rotation never orphans files another stored passphrase opens ─────

def test_rotation_is_refused_when_another_stored_passphrase_opens_files(env):
    a = [env.seal("vault/legal/a%d.vault" % i, b"a", "A") for i in range(2)]
    other = env.seal("wiki/entities/x.md", b"x", "OTHER")
    env.keyring.set_password("agent-friday", "vault-passphrase", "A")
    env.launcher("OTHER")
    assert env.vp.store("B", rotate=True) == []
    assert "orphan" in env.vp.LAST_STORE_REFUSAL
    env.vc.decrypt(other.read_bytes(), env.vc.derive_key("OTHER", env.salt))
    for f in a:
        env.vc.decrypt(f.read_bytes(), env.vc.derive_key("A", env.salt))
    assert env.keyring.get_password("agent-friday", "vault-passphrase") == "A"


# ── F6: a restore that fails is said, never claimed ────────────────────────

def test_a_failed_restore_is_reported_not_claimed(env, monkeypatch):
    env.seal("vault/legal/a.vault", b"a", "A")
    env.keyring.set_password("agent-friday", "vault-passphrase", "A")
    monkeypatch.setattr(env.vp, "_keystore_state", lambda: ("ok", "A"))
    monkeypatch.setattr(env.vp, "_rewrap_keystore", lambda p: False)
    monkeypatch.setattr(env.vp, "_undo_rotation", lambda report: ["vault/legal/a.vault"])
    assert env.vp.store("B", rotate=True) == []
    why = env.vp.LAST_STORE_REFUSAL
    assert "could NOT be put back" in why and "every file was put back" not in why


# ── F7: bounded scan; the health probe never resolves on its caller ────────

def test_sealed_files_are_looked_for_in_the_sealed_folders_only(env):
    inside = env.seal("wiki/a.md", b"a", "A")
    outside = env.seal("elsewhere/b.bin", b"b", "A")
    assert env.vp.sealed_files() == [inside]
    assert outside in env.vp.sealed_files(full=True)


def test_the_health_probe_never_resolves_on_its_callers_thread(env, monkeypatch):
    import threading
    import time
    seen = []

    def fake_resolve(use_cache=True):
        seen.append(threading.current_thread())
        return ("", "")
    env.vp.reset_cache()
    monkeypatch.setattr(env.vp, "resolve", fake_resolve)
    monkeypatch.setattr(env.vp, "_SCAN", {"done": True, "unreadable": 0})
    env.vp._integrity_probe()
    deadline = time.time() + 5
    while env.vp._RESOLVING.is_set() and time.time() < deadline:
        time.sleep(0.01)
    assert seen and threading.current_thread() not in seen


# ── F8: an unknown profile is not a redirect; keyrings.alt is fenced ───────

def test_an_unknown_profile_does_not_make_a_real_home_scratch(env, tmp_path):
    fk = _FakeKeyring()
    _install_keyring(env.monkeypatch, fk, real=True)
    _not_scratch(env, tmp_path / "account")
    env.monkeypatch.setattr(env.vp, "_real_profile_dir", lambda: None)
    env.monkeypatch.setenv("FRIDAY_HOME", str(env.home))
    assert env.vp.keychain_is_this_homes() is True


def test_a_keyrings_alt_backend_is_fenced_for_a_scratch_home(env, monkeypatch):
    fk = _FakeKeyring()
    mod = types.ModuleType("keyring")
    mod.get_password = fk.get_password
    mod.set_password = fk.set_password
    backend = type("PlaintextKeyring", (), {"__module__": "keyrings.alt.file"})
    mod.get_keyring = lambda: backend()
    monkeypatch.setitem(sys.modules, "keyring", mod)
    monkeypatch.setenv("FRIDAY_TESTING", "1")
    env.vp.store("rehearsal-passphrase")
    assert fk.store == {}, "a scratch home wrote a file-backed keyring"


# ── F9: the doctor leaves nothing from a launch script in the environment ──

def test_the_doctor_puts_the_environment_back_after_the_import(env, monkeypatch):
    doc = _doctor()
    snapshot = dict(os.environ)
    monkeypatch.setenv("FRIDAY_PASSWORD", "from-a-launch-script")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "forced-by-a-launch-script")
    fake_core = types.SimpleNamespace(ENV_FROM_LAUNCH_SCRIPTS={"FRIDAY_PASSWORD",
                                                               "ANTHROPIC_API_KEY"})
    monkeypatch.setattr(env.vp, "_from_start_bat", lambda: "")
    doc._restore_env(snapshot, fake_core)
    assert os.environ.get("FRIDAY_PASSWORD") == snapshot.get("FRIDAY_PASSWORD")
    assert os.environ.get("ANTHROPIC_API_KEY") == snapshot.get("ANTHROPIC_API_KEY")
    assert fake_core.ENV_FROM_LAUNCH_SCRIPTS == set()
    assert env.vp._from_start_bat() == "from-a-launch-script"


# ── a rotation never leaves embedded ciphertext behind ─────────────────────

def test_rotation_refuses_while_a_store_holds_embedded_ciphertext(env):
    import base64
    a = env.seal("vault/legal/a.vault", b"a", "A")
    env.keyring.set_password("agent-friday", "vault-passphrase", "A")
    row = base64.b64encode(env.vc.encrypt(b"shelf row", env.vc.derive_key("A", env.salt)))
    shelf = env.home / "library" / "owner" / "library.sqlite"
    shelf.parent.mkdir(parents=True)
    shelf.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200 + row + b"\x00tail")
    assert env.vp.store("B", rotate=True) == []
    assert "library" in env.vp.LAST_STORE_REFUSAL
    env.vc.decrypt(a.read_bytes(), env.vc.derive_key("A", env.salt))   # untouched
    assert env.keyring.get_password("agent-friday", "vault-passphrase") == "A"


# ── the graph's health probe never decrypts on its caller's thread ────────

def test_the_graph_probe_never_decrypts_on_its_callers_thread(kg, monkeypatch):
    import threading
    import time
    _sealed_entities(kg, kg.k_old, 2)
    seen = []

    def fake_open(raw):
        seen.append(threading.current_thread())
        return None, None
    monkeypatch.setattr(kg.st, "_open_sealed", fake_open)
    assert kg.st._integrity_probe() == []                 # unknown yet: checked aside
    deadline = time.time() + 5
    while kg.st._CHECKING.is_set() and time.time() < deadline:
        time.sleep(0.01)
    assert seen and threading.current_thread() not in seen
    assert kg.st._integrity_probe()[0]["state"] == "cannot_decrypt"


# ── a keystore rollback that fails is said ────────────────────────────────

def test_a_failed_keystore_rollback_is_said_not_hidden(env, monkeypatch):
    calls = []

    def rewrap(p):
        calls.append(p)
        return len(calls) == 1                     # forward works, the way back fails
    monkeypatch.setattr(env.vp, "_keystore_state", lambda: ("ok", "A-old"))
    monkeypatch.setattr(env.vp, "_rewrap_keystore", rewrap)
    monkeypatch.setattr(env.vp, "keychain_is_this_homes", lambda: False)
    monkeypatch.setattr(env.vp, "dpapi_available", lambda: False)
    assert env.vp.store("A-new") == []
    assert calls == ["A-new", "A-old"]
    why = env.vp.LAST_STORE_REFUSAL
    assert "keystore" in why and "nothing was changed" not in why


# ── resolve() never caches an answer a store() overtook ───────────────────

def test_resolve_does_not_cache_an_answer_a_store_overtook(env, monkeypatch):
    original = env.vp._candidates

    def racing():
        env.vp.reset_cache()                       # a store() lands mid-resolve
        return original()
    env.keyring.set_password("agent-friday", "vault-passphrase", "v")
    env.vp.reset_cache()
    monkeypatch.setattr(env.vp, "_candidates", racing)
    assert env.vp.resolve()[0] == "v"
    assert env.vp._CACHE is None, "a stale answer was cached"


# ── a refused save never advances the manifest ────────────────────────────

def test_a_refused_purge_leaves_the_manifest_alone(kg, monkeypatch):
    from agent_friday.services.knowledge_graph import indexer
    _sealed_entities(kg, kg.k_old, 2)
    (kg.dir / "entities.json").write_text(
        json.dumps([{"id": "e1", "provenance": {"sensitivity": 1, "docs": ["5"]}}]),
        encoding="utf-8")
    kg.legacy([])                                  # the sealed half cannot be read
    saved = []

    class FakeManifest:
        def __init__(self, *a, **k):
            pass

        def forget(self, path):
            pass

        def save(self):
            saved.append(True)
    monkeypatch.setattr(indexer, "KnowledgeGraphStore", lambda *a, **k: kg.st.KnowledgeGraphStore(kg.dir))
    monkeypatch.setattr(indexer, "KnowledgeGraphManifest", FakeManifest)
    from agent_friday.services.library import store as libstore
    monkeypatch.setattr(libstore, "store_for",
                        lambda owner: types.SimpleNamespace(get_document=lambda i: {"path": "p"}))
    import agent_friday.conversation_memory as cmem
    monkeypatch.setattr(cmem, "get_conversation_memory",
                        lambda: (_ for _ in ()).throw(RuntimeError("not in this test")))
    indexer.purge_library_documents([5])
    assert saved == [], "the manifest moved on although the sealed save was refused"


def test_the_keystore_is_not_counted_as_embedded_ciphertext(env, keystore, monkeypatch):
    monkeypatch.setattr(keystore, "KEYSTORE_PATH", env.home / "security" / "keystore.json")
    keystore.create(wrap="passphrase", passphrase="A")    # its wrap follows store() itself
    assert keystore.KEYSTORE_PATH.exists()
    assert env.vp.embedded_sealed_files() == []


# ── recovery restores only what a no-key save dropped ─────────────────────

def test_a_save_that_removed_the_last_private_records_is_never_undone(kg, env, monkeypatch):
    import agent_friday.services.agent as agent
    monkeypatch.setattr(agent, "_get_vault_key", lambda: kg.k_current)
    p, _ = _sealed_entities(kg, kg.k_old, 3)              # opens only with an older key
    kg.legacy([("launcher:FRIDAY_PASSWORD", kg.k_old)])
    store = kg.st.KnowledgeGraphStore(kg.dir)
    assert len(store.load("entities")) == 3
    store.save("entities", [])                            # a purge removed them all
    assert not p.exists()
    out = _doctor().recover_kg()
    assert out["ok"] and out["resealed"] == []
    assert not p.exists(), "removed records were restored"
    assert [x["records_not_merged"] for x in out["retired_unmerged"]] == [3]


def test_a_no_key_save_marks_its_copy_dropped_and_recovery_restores_it(kg, env, monkeypatch):
    from agent_friday.privacy import vault_crypto as vc
    import agent_friday.services.agent as agent
    p, _ = _sealed_entities(kg, kg.k_old, 3)
    kg.legacy([("launcher:FRIDAY_PASSWORD", kg.k_old)])
    monkeypatch.setattr(kg.st, "_vault_key", lambda: None)   # no key this run
    kg.st._KEYS_CACHE.clear()
    store = kg.st.KnowledgeGraphStore(kg.dir)
    store.load("entities")
    out = store.save("entities", [{"id": "s", "provenance": {"sensitivity": 3}}])
    assert out["dropped"] == 1 and not p.exists()
    assert list(kg.dir.glob("entities.sensitive.dropped-*.json"))
    monkeypatch.setattr(kg.st, "_vault_key", lambda: kg.k_current)
    monkeypatch.setattr(agent, "_get_vault_key", lambda: kg.k_current)
    kg.st._KEYS_CACHE.clear()
    rec = _doctor().recover_kg()
    assert rec["ok"] and rec["resealed"][0]["records"] == 3
    assert len(json.loads(vc.decrypt(p.read_bytes(), kg.k_current))) == 3


def test_recovery_restores_only_the_newest_dropped_copy(kg, env, monkeypatch):
    from agent_friday.privacy import vault_crypto as vc
    import agent_friday.services.agent as agent
    monkeypatch.setattr(agent, "_get_vault_key", lambda: kg.k_current)
    for stamp, rid in (("20261001T000000", "older"), ("20261002T000000", "newer")):
        (kg.dir / ("entities.sensitive.dropped-%s.json" % stamp)).write_bytes(
            vc.encrypt(json.dumps([{"id": rid}]).encode(), kg.k_old))
    kg.legacy([("launcher:FRIDAY_PASSWORD", kg.k_old)])
    out = _doctor().recover_kg()
    live = kg.dir / "entities.sensitive.json"
    assert json.loads(vc.decrypt(live.read_bytes(), kg.k_current)) == [{"id": "newer"}]
    assert len(out["retired_unmerged"]) == 1
    assert not list(kg.dir.glob("*.dropped-*.json"))


# ── a refused rotation keeps nothing ──────────────────────────────────────

def test_a_refused_rotation_keeps_nothing(env):
    env.seal("vault/legal/a.vault", b"a", "A")
    env.seal("vault/legal/b.vault", b"b", "A")
    env.seal("wiki/entities/x.md", b"x", "OTHER")
    env.keyring.set_password("agent-friday", "vault-passphrase", "A")
    env.launcher("OTHER")
    assert env.vp.store("B", rotate=True) == []
    assert _kept(env) == [], "a value was kept for a rotation that never happened"


# ── the graph's background check ──────────────────────────────────────────

def test_a_background_check_overtaken_by_a_key_change_records_nothing(kg, monkeypatch):
    from agent_friday.services import vault_passphrase as vp
    p, _ = _sealed_entities(kg, kg.k_old, 2)

    def opens_while_the_key_changes(raw):
        vp.reset_cache()                           # a store() lands mid-check
        return None, None
    monkeypatch.setattr(kg.st, "_open_sealed", opens_while_the_key_changes)
    kg.st._CHECKING.set()
    kg.st._check_in_background([p])
    assert str(p) not in kg.st.SENSITIVE_STATE
    assert not kg.st._CHECKING.is_set()


def test_a_failed_thread_start_never_shuts_the_check_gate(kg, monkeypatch):
    import threading
    _sealed_entities(kg, kg.k_old, 2)

    class NoThreads:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")
    monkeypatch.setattr(threading, "Thread", NoThreads)
    assert kg.st._integrity_probe() == []
    assert not kg.st._CHECKING.is_set()


# ── refusals are counted, not hoarded ─────────────────────────────────────

def test_refusals_are_counted_and_the_reasons_bounded(kg):
    _sealed_entities(kg, kg.k_old, 2)
    kg.legacy([])
    store = kg.st.KnowledgeGraphStore(kg.dir)
    store.load("entities")
    for _ in range(30):
        store.save("entities", [])
    assert store.refusals == 30 and len(store.refused) <= 20
