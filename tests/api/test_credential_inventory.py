"""BD-08 / NS-26.5-4: every credential's owner, change and last use, without its value.

Before: Friday could say whether a provider key was "connected", but not when a credential was
last used or changed, and nothing listed them all. Now credential_store.inventory (and
GET /api/security/credentials) lists every credential file Friday holds with its kind, owner,
protection, last change and last use; a read is recorded as use (name and time only, written at
most every ten minutes per credential). No value appears anywhere in the answer or on disk.
Synthetic values only.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import credential_store as cs
from agent_friday.services import keystore as ks

FAKE_KEY = "not-a-real-key-" + "7" * 20  # pragma: allowlist secret


@pytest.fixture
def store(tmp_path, monkeypatch):
    from agent_friday import core
    home = tmp_path / ".friday"
    monkeypatch.setattr(core, "FRIDAY_DIR", home)
    monkeypatch.setattr(cs, "_VAULT_CONFIG_FILE", home / "vault" / ".vault_config.json")
    monkeypatch.setattr(cs, "_SECURITY_DIR", home / "security")
    monkeypatch.setattr(cs, "_CRED_AUDIT_LOG", home / "security" / "credential_audit.jsonl")
    monkeypatch.setattr(cs, "_PROVIDER_KEYS_DIR", home / "providers" / "keys")
    monkeypatch.setattr(ks, "KEYSTORE_PATH", home / "security" / "keystore.json")
    ks._reset_cache_for_tests()
    cs._USE.clear()
    cs._USE_FLUSHED.clear()
    yield home
    ks._reset_cache_for_tests()
    cs._USE.clear()
    cs._USE_FLUSHED.clear()


def test_the_inventory_lists_a_credential_with_its_use_and_never_its_value(store):
    cs.set_provider_key("example", FAKE_KEY)
    rows = cs.inventory()
    row = next(r for r in rows if r["name"] == "providers/keys/example.key")
    assert row["kind"] == "AI provider key" and row["owner"] == "you"
    assert row["protection"] == "keystore" and row["last_changed"] and row["last_used"] is None
    assert cs.get_provider_key("example") == FAKE_KEY
    row = next(r for r in cs.inventory() if r["name"] == "providers/keys/example.key")
    assert row["last_used"], "a read was not recorded as use"
    assert FAKE_KEY not in json.dumps(cs.inventory())
    on_disk = (store / "security" / "credential_use.json").read_text(encoding="utf-8")
    assert "providers/keys/example.key" in on_disk and FAKE_KEY not in on_disk


def test_the_route_answers_names_and_times_only(client, store):
    cs.set_provider_key("example", FAKE_KEY)
    cs.get_provider_key("example")
    r = client.get("/api/security/credentials")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True and d["protection"] == "keystore"
    assert any(c["name"] == "providers/keys/example.key" and c["last_used"] for c in d["credentials"])
    assert FAKE_KEY not in r.get_data(as_text=True)


def test_a_status_check_is_not_a_use(store):
    cs.set_provider_key("example", FAKE_KEY)
    assert cs.provider_key_status("example") == "connected"
    row = next(r for r in cs.inventory() if r["name"] == "providers/keys/example.key")
    assert row["last_used"] is None


def test_use_is_written_at_most_every_ten_minutes(store, monkeypatch):
    cs.set_provider_key("example", FAKE_KEY)
    writes = []
    real = cs.Path.replace

    def counting(self, target):
        if str(target).endswith("credential_use.json"):
            writes.append(1)
        return real(self, target)
    monkeypatch.setattr(cs.Path, "replace", counting)
    for _ in range(25):
        cs.get_provider_key("example")
    assert len(writes) == 1
