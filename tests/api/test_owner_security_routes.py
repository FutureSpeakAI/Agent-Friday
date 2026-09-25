"""Settings -> Privacy & Approvals: re-confirming Friday's rules, and
protecting the credential keystore with the vault passphrase.

Both change a security boundary, so both answer only Friday's own page on
this PC: a request from this machine that carries the page's X-Friday-Token.
Neither touches the real home, keychain or Credential Manager: the governance
key, the pin directory, the keystore path and the passphrase resolver are
all pointed at test doubles.
"""
from __future__ import annotations

import json

import pytest

import agent_friday.core as core
from agent_friday.governance import action_gate
from agent_friday.privacy import vault_crypto as vc
from agent_friday.services import keystore as ks

CLOUDFLARED = {
    "CF-Connecting-IP": "203.0.113.9",
    "CF-Ray": "0000000000000000-LHR",
    "X-Forwarded-For": "203.0.113.9",
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": "random-words-1234.trycloudflare.com",
}
KEY_OLD_PC = b"a" * 32
KEY_NEW_PC = b"b" * 32
PASS = "vault passphrase for tests"  # pragma: allowlist secret


def _token():
    return {"X-Friday-Token": core._current_api_token()}


# ── Re-confirm Friday's rules ───────────────────────────────────────────────

@pytest.fixture
def gov(tmp_path, monkeypatch):
    monkeypatch.setattr(action_gate, "friday_home", lambda: str(tmp_path))
    key = {"k": KEY_OLD_PC}
    monkeypatch.setattr(action_gate, "_governance_key", lambda: key["k"])
    return key


def _outward():
    return action_gate.authorize("create_calendar_event", {"title": "x"},
                                 {"is_background_task": True})


def test_repin_needs_the_pages_token(client, gov):
    r = client.post("/api/governance/claws/repin", json={"confirm": True})
    assert r.status_code == 403


def test_repin_is_refused_through_a_tunnel_even_with_the_token(app, gov):
    r = app.test_client().post("/api/governance/claws/repin", json={"confirm": True},
                               headers=dict(CLOUDFLARED, **_token()),
                               environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code in (401, 403)


def test_repin_needs_an_explicit_confirmation(client, gov):
    r = client.post("/api/governance/claws/repin", json={}, headers=_token())
    assert r.status_code == 400


def test_moving_to_a_new_pc_holds_outward_actions_until_the_owner_repins(client, gov, tmp_path):
    assert action_gate.verify_claws()[0]               # pinned on the old PC
    gov["k"] = KEY_NEW_PC                              # ~/.friday moved; new key

    status = client.get("/api/governance/claws").get_json()
    assert status["state"] == "mismatch" and status["holds_outward"] is True
    v = _outward()
    assert v.action == "deny" and "cLaws" in v.reason

    r = client.post("/api/governance/claws/repin", json={"confirm": True}, headers=_token())
    assert r.status_code == 200 and r.get_json()["state"] == "intact"

    assert action_gate.verify_claws() == (True, "intact")
    assert _outward().action != "deny"
    rows = [json.loads(x) for x in (tmp_path / "decision-bom.jsonl").read_text().splitlines()]
    assert any(r.get("tool") == "governance:repin_claws" and r.get("hmac") for r in rows)


def test_status_does_not_pin_anything(client, gov, tmp_path):
    s = client.get("/api/governance/claws").get_json()
    assert s["state"] == "not_pinned" and len(s["text_sha256"]) == 64
    assert not (tmp_path / "governance" / "claws.pin.json").exists()


# ── Keystore wrap ──────────────────────────────────────────────────────────

@pytest.fixture
def keystore(tmp_path, monkeypatch):
    import agent_friday.routes.owner_security as osr
    monkeypatch.setattr(ks, "KEYSTORE_PATH", tmp_path / "security" / "keystore.json")
    monkeypatch.setattr(ks, "_derive", lambda pw, salt: vc.derive_key(pw, salt, vc.FAST_PROFILE))
    monkeypatch.setattr(ks, "_passphrase", lambda: PASS)
    monkeypatch.setattr(osr, "_vault_passphrase", lambda: (PASS, "test"))
    ks._reset_cache_for_tests()
    ks.create(wrap="none")
    blob = ks.encrypt(b"stored credential")
    yield blob
    ks._reset_cache_for_tests()


def _doc():
    return json.loads(ks.KEYSTORE_PATH.read_text(encoding="utf-8"))


def test_status_says_how_the_keystore_is_protected(client, keystore):
    s = client.get("/api/security/keystore").get_json()
    assert s["wrap"] == "none" and s["vault_passphrase_set"] is True
    assert "path" not in s


def test_wrap_needs_the_pages_token(client, keystore):
    r = client.post("/api/security/keystore/wrap",
                    json={"wrap": "passphrase", "passphrase": PASS})
    assert r.status_code == 403
    assert _doc()["wrap"] == "none"


def test_wrap_needs_the_passphrase_friday_uses(client, keystore):
    r = client.post("/api/security/keystore/wrap",
                    json={"wrap": "passphrase", "passphrase": "something else"},
                    headers=_token())
    assert r.status_code == 400
    assert _doc()["wrap"] == "none"


def test_wrap_and_unwrap_keep_every_credential_readable(client, keystore):
    r = client.post("/api/security/keystore/wrap",
                    json={"wrap": "passphrase", "passphrase": PASS}, headers=_token())
    assert r.status_code == 200 and r.get_json()["wrap"] == "passphrase"
    doc = _doc()
    assert "key" not in doc and doc["wrapped_key"]
    ks._reset_cache_for_tests()                       # a fresh start
    assert ks.decrypt(keystore) == b"stored credential"

    r = client.post("/api/security/keystore/wrap",
                    json={"wrap": "none", "passphrase": PASS}, headers=_token())
    assert r.status_code == 200 and _doc()["wrap"] == "none"
    assert ks.decrypt(keystore) == b"stored credential"


def test_changing_the_vault_passphrase_rewraps_the_keystore(keystore, monkeypatch):
    import sys
    import types
    from agent_friday.services import vault_passphrase as vp

    ks.set_wrap("passphrase", PASS)
    store: dict = {}
    fake = types.SimpleNamespace(
        set_password=lambda s, a, v: store.__setitem__((s, a), v),
        get_password=lambda s, a: store.get((s, a)))
    monkeypatch.setitem(sys.modules, "keyring", fake)
    monkeypatch.setattr(vp, "dpapi_available", lambda: False)

    assert vp.store("a brand new passphrase") == ["keychain"]

    monkeypatch.setattr(ks, "_passphrase", lambda: "a brand new passphrase")
    ks._reset_cache_for_tests()
    assert ks.decrypt(keystore) == b"stored credential"
