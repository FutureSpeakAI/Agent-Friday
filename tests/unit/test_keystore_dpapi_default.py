"""The keystore root key is wrapped with Windows DPAPI by default (threat assessment fix #2). TEST DATA ONLY.

Before: a fresh keystore was `wrap: "none"`, the 32-byte root key in keystore.json next to the ciphertext, so anyone running
as the user could decrypt every stored credential. Now: where DPAPI exists the default is `wrap: "dpapi"` (CurrentUser
scope: only that Windows account on that PC can unwrap it), an old `wrap: "none"` keystore is migrated on start with a
receipt on the action ledger, and a passphrase wrap stays something the owner chooses. What each check bites on:
  1. a new keystore is dpapi-wrapped and the file holds no copy of the root key; the same key comes back after a restart;
  2. where DPAPI does not exist the keystore is still `none`, and describe() says so in plain words (no claim it is protected);
  3. migration keeps the SAME root key (a credential sealed before still opens), writes one receipt with no key material,
     does nothing the second time, and leaves a passphrase-wrapped keystore alone;
  4. migration that cannot prove the new wrap opens leaves the file untouched and does not claim success;
  5. a dpapi keystore this account cannot unwrap raises KeystoreLocked in words and never makes a new root key;
  6. the owner can still choose a passphrase wrap (from dpapi) and go back.
DPAPI is faked with a reversible, account-bound transform so the tests run on every platform; the real calls are
vault_passphrase.dpapi (already used for the vault-passphrase file).
"""
import base64
import json

import pytest

from agent_friday.services import action_ledger as L
from agent_friday.services import keystore as ks
from tests.unit.test_friday_bundle import homes  # noqa: F401 - the two-PC fixture


def _fake_dpapi(account="alice"):
    def dpapi(data, encrypt):
        tag = account.encode() + b"|"
        if encrypt:
            return bytes(b ^ 0x5A for b in tag + data)
        raw = bytes(b ^ 0x5A for b in data)
        return raw[len(tag):] if raw.startswith(tag) else None
    return dpapi


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ks, "KEYSTORE_PATH", tmp_path / "keystore.json")
    monkeypatch.setattr(ks, "_CACHED_KEY", None)
    monkeypatch.setattr(ks, "_passphrase", lambda: "")
    monkeypatch.setattr(ks, "_dpapi", _fake_dpapi(), raising=False)
    monkeypatch.setattr(ks, "_dpapi_available", lambda: True, raising=False)
    return tmp_path


def _doc():
    return json.loads(ks.KEYSTORE_PATH.read_text(encoding="utf-8"))


def test_a_new_keystore_is_dpapi_wrapped_and_the_file_holds_no_copy_of_the_root_key(store):
    root = ks.root_key()
    doc = _doc()
    assert doc["wrap"] == "dpapi" and "key" not in doc
    text = ks.KEYSTORE_PATH.read_text(encoding="utf-8")
    assert base64.b64encode(root).decode() not in text and root.hex() not in text
    ks._reset_cache_for_tests()
    assert ks.root_key() == root, "the same key comes back after a restart"
    assert ks.describe()["wrap"] == "dpapi" and "Windows" in ks.describe()["summary"]


def test_where_dpapi_does_not_exist_the_keystore_is_none_and_says_so_plainly(store, monkeypatch):
    monkeypatch.setattr(ks, "_dpapi_available", lambda: False, raising=False)
    ks.root_key()
    assert _doc()["wrap"] == "none"
    assert "file permissions" in ks.describe()["summary"] and "Windows" not in ks.describe()["summary"]


def _old_none_keystore(monkeypatch):
    monkeypatch.setattr(ks, "_dpapi_available", lambda: False, raising=False)
    root = ks.root_key()
    sealed = ks.encrypt(b"a credential sealed before the change")
    ks._reset_cache_for_tests()
    monkeypatch.setattr(ks, "_dpapi_available", lambda: True, raising=False)
    return root, sealed


def test_migration_keeps_the_same_root_key_writes_one_receipt_and_does_nothing_the_second_time(store, monkeypatch):
    root, sealed = _old_none_keystore(monkeypatch)
    assert _doc()["wrap"] == "none"
    assert ks.migrate_default_wrap() == "migrated"
    assert _doc()["wrap"] == "dpapi" and "key" not in _doc()
    ks._reset_cache_for_tests()
    assert ks.root_key() == root and ks.decrypt(sealed) == b"a credential sealed before the change"
    receipts = L.entries(["keystore_wrap"])
    assert len(receipts) == 1 and receipts[0]["rec"]["from"] == "none" and receipts[0]["rec"]["to"] == "dpapi"
    assert base64.b64encode(root).decode() not in json.dumps(receipts[0]["rec"]) and root.hex() not in json.dumps(receipts[0]["rec"])
    assert ks.migrate_default_wrap() == "already" and len(L.entries(["keystore_wrap"])) == 1


def test_migration_leaves_a_passphrase_wrapped_keystore_alone(store, monkeypatch):
    monkeypatch.setattr(ks, "_dpapi_available", lambda: False, raising=False)
    ks.create(wrap="passphrase", passphrase="correct horse")
    before = ks.KEYSTORE_PATH.read_bytes()
    monkeypatch.setattr(ks, "_dpapi_available", lambda: True, raising=False)
    assert ks.migrate_default_wrap() == "passphrase"
    assert ks.KEYSTORE_PATH.read_bytes() == before and L.entries(["keystore_wrap"]) == []


def test_migration_that_cannot_prove_the_new_wrap_opens_leaves_the_file_untouched(store, monkeypatch):
    _old_none_keystore(monkeypatch)
    before = ks.KEYSTORE_PATH.read_bytes()
    monkeypatch.setattr(ks, "_dpapi", lambda data, encrypt: None if not encrypt else b"opaque", raising=False)
    assert ks.migrate_default_wrap() == "failed"
    assert ks.KEYSTORE_PATH.read_bytes() == before and L.entries(["keystore_wrap"]) == []


def test_a_dpapi_keystore_this_account_cannot_unwrap_raises_in_words_and_never_makes_a_new_key(store, monkeypatch):
    ks.root_key()
    before = ks.KEYSTORE_PATH.read_bytes()
    ks._reset_cache_for_tests()
    monkeypatch.setattr(ks, "_dpapi", _fake_dpapi("bob"), raising=False)
    with pytest.raises(ks.KeystoreLocked) as e:
        ks.root_key()
    assert "Windows account" in str(e.value)
    assert ks.KEYSTORE_PATH.read_bytes() == before, "no new root key, no overwrite"


def test_the_owner_can_still_choose_a_passphrase_wrap_from_dpapi_and_go_back(store, monkeypatch):
    root = ks.root_key()
    ks.set_wrap("passphrase", "correct horse")
    assert _doc()["wrap"] == "passphrase" and "key" not in _doc()
    ks._reset_cache_for_tests()
    monkeypatch.setattr(ks, "_passphrase", lambda: "correct horse")
    assert ks.root_key() == root
    ks.set_wrap("dpapi")
    assert _doc()["wrap"] == "dpapi"
    ks._reset_cache_for_tests()
    assert ks.root_key() == root


# ── backups: a Windows-account wrap cannot be opened on another PC ───────────

def test_portable_bytes_opens_a_dpapi_wrap_leaves_other_wraps_and_refuses_one_it_cannot_open(store, monkeypatch):
    root = ks.root_key()
    portable = json.loads(ks.portable_bytes(ks.KEYSTORE_PATH.read_bytes()))
    assert portable["wrap"] == "none" and base64.b64decode(portable["key"]) == root and "wrapped_key" not in portable
    plain = json.dumps({"wrap": "none", "key": base64.b64encode(root).decode()}).encode()
    assert ks.portable_bytes(plain) == plain
    other = _fake_dpapi("bob")
    monkeypatch.setattr(ks, "_dpapi", other, raising=False)
    assert ks.portable_bytes(ks.KEYSTORE_PATH.read_bytes()) is None, "another account cannot open it: not carried"


def test_a_bundle_made_on_one_pc_restores_on_another_and_start_up_wraps_it_there(homes, tmp_path, monkeypatch):
    from agent_friday.services import friday_bundle as fb
    from tests.unit.test_friday_bundle import PASS, _make, _point_at
    a, b, _key = homes
    monkeypatch.setattr(ks, "_CACHED_KEY", None)
    monkeypatch.setattr(ks, "_passphrase", lambda: "")
    monkeypatch.setattr(ks, "_dpapi", _fake_dpapi("alice"), raising=False)
    monkeypatch.setattr(ks, "_dpapi_available", lambda: True, raising=False)
    monkeypatch.setattr(ks, "KEYSTORE_PATH", a / "security" / "keystore.json")
    (a / "security" / "keystore.json").unlink()
    root = ks.root_key()
    assert json.loads((a / "security" / "keystore.json").read_text())["wrap"] == "dpapi"
    out = tmp_path / "x.fridaybundle"
    _make(a, out)
    # the other PC: another Windows account, the restored file must open and be wrapped again at start-up
    _point_at(b, monkeypatch)
    fb.restore(out, PASS, home=b)
    monkeypatch.setattr(ks, "_dpapi", _fake_dpapi("carol"), raising=False)
    monkeypatch.setattr(ks, "KEYSTORE_PATH", b / "security" / "keystore.json")
    ks._reset_cache_for_tests()
    assert json.loads(ks.KEYSTORE_PATH.read_text())["wrap"] == "none", "the bundle carried the portable form"
    assert ks.migrate_default_wrap() == "migrated"
    ks._reset_cache_for_tests()
    assert json.loads(ks.KEYSTORE_PATH.read_text())["wrap"] == "dpapi" and ks.root_key() == root
