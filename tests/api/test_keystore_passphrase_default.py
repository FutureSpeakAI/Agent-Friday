"""A21: the key store's passphrase is on by default (owner ruling 2026-10-21).

New installs: setup locks the key store's root key with the vault passphrase unless the owner
unticks it (or skips the passphrase); the words say what it protects and what losing it costs.
Existing installs: one card offers it, once; nothing is forced. Synthetic passphrases only.

The offer module is imported inside the tests that need it, so the setup tests fail on the old
code by assertion (setup did not lock the keys), not by a missing import.
"""
from __future__ import annotations

import json

import pytest

from agent_friday import core
from agent_friday.services import keystore as ks

PASS = "synthetic-test-passphrase-41"  # pragma: allowlist secret


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / ".friday"
    h.mkdir()
    monkeypatch.setattr(core, "FRIDAY_DIR", h)
    monkeypatch.setattr(core, "_SETUP_MARKER", h / ".setup_complete", raising=False)
    monkeypatch.setattr(core, "SETTINGS_FILE", h / "settings.json", raising=False)
    monkeypatch.setattr(ks, "KEYSTORE_PATH", h / "security" / "keystore.json")
    for var in ("FRIDAY_VAULT_PASSPHRASE", "FRIDAY_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    from agent_friday.services import vault_passphrase as vp
    kept = {"pw": ""}

    def store(pw):                                  # a durable home took it
        kept["pw"] = pw
        return ["test-home"]
    monkeypatch.setattr(vp, "store", store)
    monkeypatch.setattr(vp, "durable_value", lambda: (kept["pw"], "test home"))
    monkeypatch.setattr(vp, "_from_start_bat", lambda: "")
    monkeypatch.setattr(vp, "_from_dpapi_file", lambda: "")
    import sys
    monkeypatch.setitem(sys.modules, "keyring", None)
    vp.reset_cache()
    ks._reset_cache_for_tests()
    ks.root_key()                                  # an existing, unlocked key store
    yield h
    ks._reset_cache_for_tests()
    vp.reset_cache()


def _ko():
    from agent_friday.services import keystore_offer
    return keystore_offer


def test_the_setup_screen_says_what_it_protects_and_what_losing_it_costs():
    from agent_friday.services import onboarding_copy as oc
    s = oc.screen("vault")
    words = " ".join(s["blocks"])
    assert oc.VAULT_KEYS in s["blocks"]
    assert "saved keys" in words and "sign in again" in words and "lost" in words
    assert s["keys_tick"] and "recommended" in s["keys_tick"]


def test_a_new_install_locks_the_saved_keys_by_default(home, monkeypatch):
    from agent_friday.services import setup_complete as sc
    assert ks.describe()["wrap"] == "none"
    report = {}
    assert sc.store_vault_passphrase(PASS, report=report) is True
    assert ks.describe()["wrap"] == "passphrase"
    assert report["keys_locked"] is True, "the setup answer says the keys were locked"
    ks._reset_cache_for_tests()
    assert ks.root_key(use_cache=False), "the next start cannot unwrap it"
    assert _ko().state()["decided"] == "protected" and _ko().state()["via"] == "setup"


def test_a_fresh_install_without_a_key_store_yet_is_locked_too(home):
    from agent_friday.services import setup_complete as sc
    ks.KEYSTORE_PATH.unlink()
    ks._reset_cache_for_tests()
    assert sc.store_vault_passphrase(PASS) is True
    assert ks.describe()["wrap"] == "passphrase"


def test_a_passphrase_kept_only_in_this_process_never_locks_the_keys(home, monkeypatch):
    """No durable home took the passphrase: locking with it would lock the owner out at the next
    start. The keys stay as they were and the record says it failed."""
    from agent_friday.services import setup_complete as sc
    from agent_friday.services import vault_passphrase as vp
    monkeypatch.setattr(vp, "store", lambda pw: [])
    monkeypatch.setattr(vp, "durable_value", lambda: ("", ""))
    report = {}
    sc.store_vault_passphrase(PASS, report=report)
    assert ks.describe()["wrap"] == "none"
    assert _ko().state()["decided"] == "failed"
    assert report["keys_locked"] is False and "not locked" in report["keys_why"], \
        "the setup answer says why, not just that it did not happen"


def test_a_lock_that_did_not_work_is_offered_again_with_the_reason(home, monkeypatch, pushes):
    """No silent refusal: the owner wanted the lock and did not get it. After setup the one-time
    card comes, and says why."""
    from agent_friday.services import setup_complete as sc
    from agent_friday.services import vault_passphrase as vp
    monkeypatch.setattr(vp, "store", lambda pw: [])
    monkeypatch.setattr(vp, "durable_value", lambda: ("", ""))
    sc.store_vault_passphrase(PASS)
    (home / ".setup_complete").write_text("done", encoding="utf-8")
    assert _ko().push_card_if_pending() == "shown"
    assert pushes[0]["body"].startswith("Setup tried to lock your saved keys and could not")


def test_a_wrap_that_cannot_be_reopened_is_undone(home, monkeypatch):
    """The proof step (the next start's unwrap) fails: the key store goes back to how it was,
    every saved secret still opens, and the answer says so."""
    blob = ks.encrypt(b"stored credential")
    real = ks.root_key

    def flaky(*a, **kw):
        if kw.get("use_cache") is False:
            raise RuntimeError("unwrap check zq7")
        return real(*a, **kw)
    monkeypatch.setattr(ks, "root_key", flaky)
    ok, why = _ko().protect_keystore(PASS)
    monkeypatch.setattr(ks, "root_key", real)
    assert ok is False and "Settings" in why
    assert ks.describe()["wrap"] == "none"
    ks._reset_cache_for_tests()
    assert ks.decrypt(blob) == b"stored credential"


def test_unticking_it_on_the_setup_screen_leaves_the_keys_as_they_were(home, monkeypatch):
    from agent_friday.services import setup_complete as sc
    (home / "onboarding.json").write_text(json.dumps({"keys_unprotected": True}), encoding="utf-8")
    assert sc.store_vault_passphrase(PASS) is True
    assert ks.describe()["wrap"] == "none"
    assert _ko().state()["decided"] == "skipped"


def test_the_setup_screen_records_an_explicit_skip(client, home):
    r = client.post("/api/onboarding/acks", json={"vault_skipped": True, "keys_unprotected": True})
    assert r.status_code == 200
    assert _ko().state()["decided"] == "skipped"
    assert _ko().push_card_if_pending() == "decided", "an install that answered setup gets no card"


def test_a_skip_after_setup_does_not_hide_the_card(client, home, pushes):
    """Once setup is finished the acks route cannot record a skip: the card is where to decide."""
    (home / ".setup_complete").write_text("done", encoding="utf-8")
    r = client.post("/api/onboarding/acks", json={"keys_unprotected": True})
    assert r.status_code == 200
    assert _ko().state() == {}
    assert _ko().push_card_if_pending() == "shown"


@pytest.fixture
def pushes(monkeypatch):
    import agent_friday.notifications_engine as ne
    seen = []
    monkeypatch.setattr(ne, "push", lambda **kw: seen.append(kw) or {"id": "n1"})
    return seen


@pytest.mark.parametrize("answer", [None, {}, {"id": "n2", "logged": True},
                                    {"id": "n3", "pull_only": True}, {"suppressed": True}],
                         ids=["none", "empty", "logged", "pull_only", "suppressed"])
def test_a_card_that_never_reached_the_screen_is_not_counted(home, monkeypatch, answer):
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: answer)
    (home / ".setup_complete").write_text("done", encoding="utf-8")
    assert _ko().push_card_if_pending() == "failed"
    assert _ko().state().get("card") != "shown", "tried again at the next start"


def test_an_existing_install_is_offered_it_once(home, pushes):
    (home / ".setup_complete").write_text("done", encoding="utf-8")
    assert _ko().push_card_if_pending() == "shown"
    assert len(pushes) == 1 and pushes[0]["kind"] == "keystore_offer" and pushes[0]["emitter"] == "KS1"
    assert pushes[0]["tier"] == "needs_you", "a choice for the owner, not a passing update"
    assert "enter those keys" in pushes[0]["body"] and "optional" in pushes[0]["body"].lower()
    assert _ko().push_card_if_pending() == "decided" and len(pushes) == 1
    assert ks.describe()["wrap"] == "none", "the card forces nothing"


def test_no_card_before_setup_or_when_already_locked(home, pushes, monkeypatch):
    assert _ko().push_card_if_pending() == "setup_pending" and pushes == []
    (home / ".setup_complete").write_text("done", encoding="utf-8")
    ks.set_wrap("passphrase", PASS)
    monkeypatch.setenv("FRIDAY_VAULT_PASSPHRASE", PASS)
    assert _ko().push_card_if_pending() == "already" and pushes == []


def test_the_card_is_a_registered_notice():
    from agent_friday.services import notice_registry as nr
    ids = {e.id: e for e in nr._ALL}
    assert "KS1" in ids and ids["KS1"].kind == "keystore_offer"
    assert ids["KS1"].where == "services/keystore_offer.py:push_card_if_pending"
