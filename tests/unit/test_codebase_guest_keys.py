"""Guest keys (salon spec §4.7 "Whose key", §11 failure table).

Another party's key, stored in the credential store under a name only this
codebase uses, usable only by this codebase's calls, removable in one click.
Removal deletes it and says so. A rejected guest key turns the header red
and names whose key failed; nothing falls back to the owner's key. The key
itself never appears in a record, a line or a response.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs
from agent_friday.services import credential_store as cs

SECRET = "sk-ant-guest-test-000000000000"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(cb, "_resident_brain", lambda: ("bonsai2:27b", "Bonsai2"))
    monkeypatch.setattr(cs, "_PROVIDER_KEYS_DIR", tmp_path / "keys")
    from agent_friday.services import cost_meter as cm
    monkeypatch.setattr(cm, "codebase_total", lambda cid, rng="all": 0.0)
    monkeypatch.setattr(cm, "codebase_costs", lambda cid, rng="all": {"total_usd": 0.0, "by_key_profile": {}, "calls": 0})
    yield


def _new():
    conv = convs.create("Rent tracker")
    return cb.create("Rent tracker", conversation_id=conv["id"]), conv


def test_a_guest_key_is_stored_under_this_codebase_only_and_never_written_down():
    rec, conv = _new()
    meta = cb.add_guest_key(rec["id"], "Alex", "anthropic", SECRET, cap_usd=5.0)
    assert meta["label"] == "Alex" and meta["provider"] == "anthropic" and meta["cap_usd"] == 5.0 and meta["added_at"]
    assert SECRET not in json.dumps(meta)
    keys = cb.guest_keys(rec["id"])
    assert [k["label"] for k in keys] == ["Alex"] and SECRET not in json.dumps(keys)
    # The store name belongs to this codebase, and the secret reads back only through the store.
    assert cb.guest_key_secret(rec["id"], "Alex") == SECRET
    assert meta["store_name"].startswith("codebase_" + rec["id"].replace("-", "")[:0] + "codebase"[:0]) or meta["store_name"].startswith("codebase_")
    assert rec["id"].replace("-", "") in meta["store_name"].replace("-", "")
    # Nothing of it in the chat line either.
    lines = [m["text"] for m in convs.messages(conv["id"]) if m.get("kind") == "seat_change"]
    assert lines and "Alex" in lines[-1] and SECRET not in " ".join(lines)
    # A second codebase does not see it.
    other, _ = _new()
    assert cb.guest_keys(other["id"]) == [] and cb.guest_key_secret(other["id"], "Alex") is None


def test_adding_is_checked_and_the_profile_can_then_be_chosen():
    rec, _ = _new()
    with pytest.raises(ValueError):
        cb.add_guest_key(rec["id"], "", "anthropic", SECRET)
    with pytest.raises(ValueError):
        cb.add_guest_key(rec["id"], "Alex", "anthropic", "")
    with pytest.raises(ValueError):
        cb.add_guest_key(rec["id"], "Alex", "mystery-cloud", SECRET)
    cb.add_guest_key(rec["id"], "Alex", "anthropic", SECRET)
    with pytest.raises(ValueError):
        cb.add_guest_key(rec["id"], "alex", "anthropic", SECRET)      # same label, any case
    assert cb.set_key_profile(rec["id"], "Alex")["key_profile"] == "Alex"
    h = cb.header(rec["id"])
    assert "Alex's key" in h["text"] and h["red"] is False


def test_removing_deletes_the_key_says_so_and_returns_the_codebase_to_the_owners_key():
    rec, conv = _new()
    cb.add_guest_key(rec["id"], "Alex", "anthropic", SECRET)
    cb.set_key_profile(rec["id"], "Alex")
    assert cb.remove_guest_key(rec["id"], "Alex") is True
    assert cb.guest_keys(rec["id"]) == [] and cb.guest_key_secret(rec["id"], "Alex") is None
    assert cb.load(rec["id"])["key_profile"] == "mine"
    line = [m["text"] for m in convs.messages(conv["id"]) if m.get("kind") == "seat_change"][-1]
    assert "deleted" in line.lower() and "your key" in line.lower()
    assert cb.remove_guest_key(rec["id"], "Alex") is False


def test_a_rejected_guest_key_turns_the_header_red_and_never_falls_back():
    rec, conv = _new()
    cb.add_guest_key(rec["id"], "Alex", "anthropic", SECRET)
    cb.set_key_profile(rec["id"], "Alex")
    cb.mark_key_rejected(rec["id"], "Alex", "authentication failed (401)")
    h = cb.header(rec["id"])
    assert h["red"] is True and "Alex" in h["note"] and "your key" in h["note"].lower()
    assert cb.load(rec["id"])["key_profile"] == "Alex"           # still Alex's: no silent substitution
    line = [m["text"] for m in convs.messages(conv["id"]) if m.get("kind") == "seat_change"][-1]
    assert "rejected" in line.lower() and "Alex" in line
    cb.clear_key_rejected(rec["id"])
    assert cb.header(rec["id"])["red"] is False


def test_the_payers_cap_is_the_users_limit_and_friday_adds_none(monkeypatch):
    rec, _ = _new()
    cb.add_guest_key(rec["id"], "Alex", "anthropic", SECRET, cap_usd=1.0)
    from agent_friday.services import cost_meter as cm
    monkeypatch.setattr(cm, "codebase_costs", lambda cid, rng="all": {"total_usd": 1.2, "by_key_profile": {"Alex": 1.2}, "calls": 4})
    over = cb.guest_key_over_cap(rec["id"], "Alex")
    assert over == {"spent": 1.2, "cap": 1.0}
    cb.add_guest_key(rec["id"], "Sam", "anthropic", SECRET)             # no cap: none is invented
    assert cb.guest_key_over_cap(rec["id"], "Sam") is None


def test_the_turns_guest_key_comes_from_the_session_context():
    rec, conv = _new()
    cb.add_guest_key(rec["id"], "Alex", "anthropic", SECRET)
    g = cb.guest_key_for_turn({"codebase": rec["id"], "key_profile": "Alex"})
    assert g["label"] == "Alex" and g["provider"] == "anthropic" and g["secret"] == SECRET and g["codebase"] == rec["id"]
    assert cb.guest_key_for_turn({"codebase": rec["id"], "key_profile": "mine"}) is None
    assert cb.guest_key_for_turn({}) is None
    assert cb.guest_key_for_turn({"codebase": rec["id"], "key_profile": "Nobody"}) is None
