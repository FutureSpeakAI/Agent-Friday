"""After a restore, outward actions stay held until this PC's governance key
checks the restored receipts and every remote channel that is switched on has
its sign-in here again or is switched off (north star §27.6-2)."""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json

import pytest


def _rr():
    assert importlib.util.find_spec("agent_friday.services.restore_reestablish") is not None, \
        "nothing checks what a restored PC still needs before outward actions resume"
    return importlib.import_module("agent_friday.services.restore_reestablish")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / ".friday"
    (h / "restore").mkdir(parents=True)
    (h / "restore" / "hold.json").write_text(json.dumps({"bundle_id": "fb-test"}), encoding="utf-8")
    (h / "ledger").mkdir()
    (h / "ledger" / "actions.jsonl").write_text('{"seq": 1}\n', encoding="utf-8")
    monkeypatch.setenv("FRIDAY_HOME", str(h))
    from agent_friday.services import action_ledger, friday_bundle
    monkeypatch.setattr(friday_bundle, "_current_governance_key", lambda home: "ab" * 32)
    monkeypatch.setattr(action_ledger, "verify", lambda **k: {"ok": True, "problems": [], "foreign": 0})
    monkeypatch.setattr(action_ledger, "append", lambda kind, rec, **k: {"kind": kind})
    return h


def _codes(rr, h):
    return [b["code"] for b in rr.blockers(h)]


def test_a_restored_pc_with_its_key_and_no_channels_needs_nothing(home):
    assert _rr().blockers(home) == []


@pytest.mark.parametrize("key,code", [(None, "governance_key_missing"), ("unreadable", "governance_key_unreadable")])
def test_no_readable_governance_key_holds_the_resume(home, monkeypatch, key, code):
    rr = _rr()
    from agent_friday.services import friday_bundle
    monkeypatch.setattr(friday_bundle, "_current_governance_key", lambda home: key)
    assert _codes(rr, home) == [code]
    with pytest.raises(friday_bundle.NotReestablished) as e:
        friday_bundle.resume_after_restore("owner:test")
    assert e.value.blockers[0]["code"] == code
    assert (home / "restore" / "hold.json").exists()           # still held


def test_receipts_signed_with_another_key_hold_the_resume(home, monkeypatch):
    rr = _rr()
    from agent_friday.services import action_ledger
    monkeypatch.setattr(action_ledger, "verify", lambda **k: {"ok": False, "problems": [{"segment": "key"}]})
    assert _codes(rr, home) == ["receipts_other_key"]
    other = {"segment": "actions.jsonl", "seq": 4, "reason": rr.OTHER_KEY, "acknowledged": False}
    monkeypatch.setattr(action_ledger, "verify", lambda **k: {"ok": False, "problems": [other]})
    assert _codes(rr, home) == ["receipts_other_key"]
    seen = dict(other, acknowledged=True)            # the owner already looked at it: no dead end
    monkeypatch.setattr(action_ledger, "verify", lambda **k: {"ok": True, "problems": [seen]})
    assert rr.blockers(home) == []


def test_a_real_receipt_signed_by_another_friday_is_caught(tmp_path, monkeypatch):
    rr = _rr()
    import sys
    from agent_friday.governance import proof_of_integrity as poi
    from agent_friday.services import action_ledger, friday_bundle
    h = tmp_path / ".friday"
    (h / "vault").mkdir(parents=True)
    monkeypatch.setenv("FRIDAY_HOME", str(h))
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", h / "vault" / ".governance-key")
    monkeypatch.setitem(sys.modules, "keyring", None)
    (h / "vault" / ".governance-key").write_bytes(b"A" * 32)
    action_ledger.reset_caches()
    action_ledger.append("decision", {"tool": "x", "decision": "allow"}, durable=True)
    (h / "vault" / ".governance-key").write_bytes(b"B" * 32)       # this PC holds a different key
    action_ledger.reset_caches()
    try:
        assert "receipts_other_key" in _codes(rr, h) or "receipts_unchecked" in _codes(rr, h)
        assert friday_bundle._current_governance_key(h)            # a key is present: not "missing"
    finally:
        action_ledger.reset_caches()


def test_no_receipts_came_so_there_is_no_key_to_check(home, monkeypatch):
    rr = _rr()
    from agent_friday.services import friday_bundle
    (home / "ledger" / "actions.jsonl").unlink()
    monkeypatch.setattr(friday_bundle, "_current_governance_key", lambda home: None)
    assert rr.blockers(home) == []


def test_a_channel_that_is_on_needs_its_sign_in_here_or_to_be_switched_off(home, monkeypatch):
    rr = _rr()
    from agent_friday.services import credential_store
    status = {"channel_telegram": "missing", "channel_discord": "connected"}
    monkeypatch.setattr(credential_store, "provider_key_status", lambda p: status[p])
    cfg = {"enabled": True, "telegram": {"enabled": True}, "discord": {"enabled": True}}
    (home / "channels.json").write_text(json.dumps(cfg), encoding="utf-8")
    left = rr.blockers(home)
    assert [(b["code"], b["what"]) for b in left] == [("channel_signin", "telegram")]
    assert "Telegram" in left[0]["said"] and "switch it off" in left[0]["said"]
    cfg["telegram"]["enabled"] = False
    (home / "channels.json").write_text(json.dumps(cfg), encoding="utf-8")
    assert rr.blockers(home) == []
    cfg["telegram"]["enabled"], cfg["enabled"] = True, False      # all channels off
    (home / "channels.json").write_text(json.dumps(cfg), encoding="utf-8")
    assert rr.blockers(home) == []


def test_the_phone_line_needs_its_twilio_key_here_or_to_be_switched_off(home, monkeypatch):
    rr = _rr()
    from agent_friday.phone import config as phone_config
    (home / "phone").mkdir()
    (home / "phone" / "config.json").write_text(json.dumps({"enabled": True}), encoding="utf-8")
    monkeypatch.setattr(phone_config, "api_credentials", lambda: None)
    assert _codes(rr, home) == ["phone_signin"]
    monkeypatch.setattr(phone_config, "api_credentials", lambda: ("AC1", "SK1", "s"))   # pragma: allowlist secret
    assert rr.blockers(home) == []


def test_a_check_that_fails_holds(home, monkeypatch):
    rr = _rr()

    def boom(h):
        raise OSError("disk")
    monkeypatch.setattr(rr, "_channels", boom)
    assert _codes(rr, home) == ["check_failed"]


def test_with_nothing_missing_the_resume_lifts_the_hold(home):
    _rr()
    from agent_friday.services import friday_bundle
    assert friday_bundle.resume_after_restore("owner:test") is True
    assert not (home / "restore" / "hold.json").exists()


def test_the_command_line_names_what_is_missing_and_does_not_resume(home, monkeypatch, capsys):
    _rr()
    from agent_friday import cli
    from agent_friday.services import friday_bundle
    monkeypatch.setattr(friday_bundle, "_current_governance_key", lambda home: None)
    rv = cli.cmd_resume(argparse.Namespace(status=False, yes=True, home=None))
    out = capsys.readouterr().out
    assert rv == 1 and "Still needed" in out and "governance key" in out
    assert (home / "restore" / "hold.json").exists()
