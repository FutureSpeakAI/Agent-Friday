"""The Locks page (first-run spec C13): every line from the store that decides it.

Nothing is claimed that is not read: an unknown disk state says "I couldn't
tell", a changed record is named, what is not built says so, and the honest
limit is always there.
"""
from __future__ import annotations

import json
import time

import pytest


@pytest.fixture
def locks(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    import agent_friday.core as core
    from agent_friday.services import egress_gate, locks, update_check
    monkeypatch.setattr(egress_gate, "_DEFAULT_LOG", tmp_path / "egress-log.jsonl")
    monkeypatch.setattr(core, "_VAULT_ENCRYPTION_STATE", {"enabled": False}, raising=False)
    monkeypatch.setattr(update_check, "status", lambda: {"enabled": True, "last_checked_at": "2026-10-15 09:00",
                                                         "verification": "unsigned"})
    return locks


def _lines(view):
    return {line["key"]: line for line in view["lines"]}


def test_every_line_is_there_and_the_honest_limit_is_always_shown(locks):
    v = locks.view()
    assert [x["key"] for x in v["lines"]] == ["lock", "recovery", "stores", "disk", "where", "keys",
                                               "records", "held", "updates", "forget"]
    assert "anything you run on this PC under your own Windows account could read" in v["honest_limit"]


def test_the_disk_line_never_guesses(locks, monkeypatch):
    lines = _lines(locks.view())
    assert lines["disk"]["state"] == "unknown" and "couldn't tell" in lines["disk"]["words"]
    monkeypatch.setattr(locks, "DISK_STATE", lambda: "off")
    assert "is off" in _lines(locks.view())["disk"]["words"]


def test_a_missing_lock_and_what_is_not_built_are_said_plainly(locks):
    lines = _lines(locks.view())
    assert lines["lock"]["state"] == "off" and "readable on this disk" in lines["lock"]["words"]
    assert lines["recovery"]["state"] == "not_built" and "no recovery kit yet" in lines["recovery"]["words"]
    assert lines["stores"]["state"] == "not_built" and "not encrypted yet" in lines["stores"]["words"]


def test_what_left_this_pc_today_is_counted_by_destination(locks, tmp_path):
    now = time.time()
    rows = [{"ts": now, "provider": "unsub.shop.example", "action": "allow"},
            {"ts": now, "provider": "unsub.shop.example", "action": "allow"},
            {"ts": now, "provider": "anthropic", "action": "block"},
            {"ts": now - 3 * 86400, "provider": "old.example", "action": "allow"}]
    (tmp_path / "egress-log.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    where = _lines(locks.view())["where"]
    assert where["today"] == [{"destination": "anthropic", "sent": 0, "stopped": 1},
                              {"destination": "unsub.shop.example", "sent": 2, "stopped": 0}]
    assert where["today_words"] == "2 requests left this PC today."


def test_a_changed_record_is_named(locks):
    from agent_friday.services import setup_record
    setup_record.append("welcome", "type", "you")
    setup_record.append("offer", "none", "default")
    assert _lines(locks.view())["records"]["state"] == "ok"
    p = setup_record.ledger_path()
    lines = p.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["decision"] = "talk"
    lines[0] = json.dumps(rec, sort_keys=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rec_line = _lines(locks.view())["records"]
    assert rec_line["state"] == "changed" and "Setup record" in rec_line["words"]


def test_the_updates_line_reports_the_manifest_check(locks):
    up = _lines(locks.view())["updates"]
    assert "no signed manifest" in up["words"] and "not code-signed yet" in up["installer"]


def test_only_named_windows_pages_open(locks):
    assert locks.open_windows_page("cmd.exe")["ok"] is False
    assert set(locks.WINDOWS_PAGES) == {"device_encryption", "other_users"}
