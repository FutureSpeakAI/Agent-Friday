"""Every change to what guards outward actions is written down, with its source.

On 09-29 the approval scanner went from Laya-union to keyword-only between
01:41 and 01:43. The only trace was a chat notice two minutes later, written
when the next turn happened to read the settings; nothing said which route
made the change or why. A gate that can be switched off without a record is
one whose history cannot be audited.

So a change to `decision_backend` or `decision_shadow` is recorded at the
moment it is saved: from, to, the named mode on each side, the route that
made it, and a reason when the caller gave one. The record holds no
conversation content, so it is kept off the record too.
"""
from __future__ import annotations

import json

from agent_friday.core import _save_settings
from agent_friday.services import decisions


def _events():
    p = decisions.gate_events_path()
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _reset(mode_settings):
    _save_settings(mode_settings)
    p = decisions.gate_events_path()
    if p.exists():
        p.unlink()


def test_a_python_write_is_recorded_with_both_sides():
    _reset({"decision_backend": "laya-union", "decision_shadow": ""})
    _save_settings({"decision_backend": "keyword", "decision_shadow": ""})
    ev = _events()
    assert len(ev) == 1
    e = ev[0]
    assert e["event"] == "gate_mode"
    assert e["from"] == {"decision_backend": "laya-union", "decision_shadow": ""}
    assert e["to"] == {"decision_backend": "keyword", "decision_shadow": ""}
    assert e["from_mode"] == "on" and e["to_mode"] == "off"
    assert e["source"] == "python"
    assert e["at"]


def test_the_settings_route_is_named_with_a_reason(client):
    _reset({"decision_backend": "keyword", "decision_shadow": ""})
    r = client.post("/api/settings",
                    json={"settings": {"decision_backend": "keyword", "decision_shadow": "laya"}},
                    headers={"X-Friday-Change-Reason": "week-one review: shadow"})
    assert r.status_code == 200
    ev = _events()
    assert len(ev) == 1
    assert ev[0]["source"] == "POST /api/settings"
    assert ev[0]["reason"] == "week-one review: shadow"
    assert ev[0]["to_mode"] == "shadow"


def test_an_unrelated_save_records_nothing():
    _reset({"decision_backend": "keyword", "decision_shadow": "laya"})
    _save_settings({"temperature": 0.5})
    assert _events() == []


def test_it_is_kept_off_the_record(monkeypatch):
    from agent_friday.services import off_record
    _reset({"decision_backend": "keyword", "decision_shadow": "laya"})
    monkeypatch.setattr(off_record, "skip", lambda *a, **k: True)
    _save_settings({"decision_backend": "keyword", "decision_shadow": ""})
    assert [e["to_mode"] for e in _events()] == ["off"]


def test_a_boot_records_where_the_gate_starts(monkeypatch):
    from agent_friday import server
    from agent_friday.services import laya_backend
    _reset({"decision_backend": "keyword", "decision_shadow": "laya"})
    monkeypatch.setattr(server, "_TESTING", False)
    monkeypatch.setattr(laya_backend, "start_warming", lambda *a, **k: None)
    server._register_decision_backends()
    ev = _events()
    assert [e["event"] for e in ev] == ["boot"]
    assert ev[0]["to_mode"] == "shadow"
