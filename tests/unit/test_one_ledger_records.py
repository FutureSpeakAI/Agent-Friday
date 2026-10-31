"""One ledger (north star §37.2-5): what Friday did that is kept in a file of
its own is also an entry in the action ledger: a spend halt, a checkpoint
setting change, a file grant event and a setting changed on a card (and its
Undo). Each entry carries ids, kinds and numbers, never content: not what was
paused, not a file's path, not a setting's old or new value."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SECRET = "taxes-2026-private-xq7"  # pragma: allowlist secret


@pytest.fixture
def got(tmp_path, monkeypatch):
    from agent_friday.services import action_ledger
    out = []
    monkeypatch.setattr(action_ledger, "try_append", lambda kind, rec, **k: out.append((kind, rec)) or {"kind": kind})
    return out


def _only(got, kind):
    rows = [r for k, r in got if k == kind]
    assert rows, "no %s entry reached the action ledger" % kind
    assert SECRET not in json.dumps(rows), "content reached the ledger"
    return rows


def test_a_spend_halt_is_in_the_ledger(got, tmp_path, monkeypatch):
    from agent_friday.services import spend_guard as sg
    monkeypatch.setattr(sg, "HALT_LOG", tmp_path / "spend_halts.jsonl")
    monkeypatch.setattr(sg, "_push", lambda *a, **k: None)
    sg._record_halt({"period": "day", "spend": 5.0, "limit": 4.0}, "anthropic", "draft about " + SECRET,
                    {"task_id": "t-1", "schedule_id": "a schedule named after " + SECRET})
    r = _only(got, "spend_halt")[0]
    assert r["provider"] == "anthropic" and r["limit"] == 4.0 and r["task_id"] == "t-1"
    assert "schedule_id" not in r                                # not id-shaped: left out


def test_a_checkpoint_setting_change_is_in_the_ledger(got, tmp_path, monkeypatch):
    from agent_friday.services import decisions
    monkeypatch.setattr(decisions, "gate_events_path", lambda: tmp_path / "gate_events.jsonl")
    decisions.record_gate_event("boot", to={"decision_backend": "local"})        # before the ledger's own check
    decisions.on_settings_change({"outward_reads": "card"}, {"outward_reads": "observe"})
    r = _only(got, "gate_event")
    assert len(r) == 1 and r[0]["event"] == "gate_mode"
    assert r[0]["from_outward_reads"] == "card" and r[0]["to_outward_reads"] == "observe"
    decisions.record_gate_event("gate_mode", **{"from": {}, "to": {"outward_reads": SECRET + " " * 3},
                                                "reason": SECRET})
    assert "reason" not in _only(got, "gate_event")[-1]          # a caller's words never go in


def test_a_file_grant_event_is_in_the_ledger_without_its_path(got, tmp_path, monkeypatch):
    from agent_friday.services import file_grants as fg
    monkeypatch.setattr(fg, "_ledger_path", lambda: tmp_path / "file_grants.jsonl")
    monkeypatch.setattr(fg, "_secret_bytes", lambda: b"k" * 32)
    fg._append_event({"event": "grant_file", "id": "g-1", "type": "file", "path": "C:/" + SECRET + ".pdf"})
    fg._append_event({"event": "revoke", "id": "r-1", "target_id": "g-1"})
    rows = _only(got, "file_grant")
    assert [(r["event"], r["id"]) for r in rows] == [("grant_file", "g-1"), ("revoke", "r-1")]
    assert rows[1]["target_id"] == "g-1" and all("path" not in r for r in rows)


def test_a_setting_changed_on_a_card_and_its_undo_are_in_the_ledger(got, monkeypatch):
    from agent_friday.services import setting_proposals as sp
    store = []
    monkeypatch.setattr(sp, "_read", lambda: list(store))
    monkeypatch.setattr(sp, "_write", lambda rows: store.__init__(rows))
    monkeypatch.setattr(sp, "_tell_pages", lambda path: None)
    import agent_friday.core as core
    monkeypatch.setattr(core, "_save_settings", lambda d: None)
    row = sp.record_change("set_theme", label="Theme", old=SECRET, new=SECRET + "-new", snapshot={},
                           by="owner", card_id="c-1")
    out = sp.undo(change_id=row["id"])
    assert out["ok"], out
    rows = _only(got, "setting_change")
    assert rows[0]["change"] == row["id"] and rows[0]["card_id"] == "c-1" and rows[1]["undone"] is True
    assert all("old" not in r and "new" not in r for r in rows)


def test_the_activity_page_names_each_new_kind():
    src = (ROOT / "src" / "agent_friday" / "routes" / "ledger.py").read_text(encoding="utf-8")
    for kind in ("spend_halt", "gate_event", "file_grant", "setting_change"):
        assert '"%s":' % kind in src, "the Activity page has no name for %s" % kind
