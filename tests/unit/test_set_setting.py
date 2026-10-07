"""Settings by sentence (HIG 6.3, 6.4): set_setting(path, value) shows the diff and waits for the owner's Yes.

Nothing is applied before the Yes; a conditional spoken yes does not apply and becomes a revision; the Yes
applies it with provenance ("Friday, by a proposal you accepted") and thirty days of Undo; a No changes nothing.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.core import _load_settings, _save_settings
from agent_friday.governance import action_gate as ag
from agent_friday.services import agent, approvals as ap, desktop_bus, local_context, taint
from agent_friday.services import setting_proposals as sp


@pytest.fixture
def world(tmp_path, monkeypatch, friday_dir):
    from agent_friday.services import dissent_gate as dg
    desktop_bus.reset()
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    monkeypatch.setattr(sp, "_store", lambda: tmp_path / "setting_changes.json")
    _save_settings({"big_mode": "off"})
    yield
    desktop_bus.reset()


def _card():
    rows = [r for r in ap.list_approvals(kind="governed_action") if (r.get("payload") or {}).get("handler") == sp.HANDLER]
    rows.sort(key=lambda r: (r.get("status") == "pending", r.get("created_at") or 0))
    return rows[-1] if rows else None


def _set(path="settings.accessibility.big_mode", value="on", **kw):
    return agent._tool_set_setting(dict({"path": path, "value": value}, **kw))


def test_the_diff_is_shown_and_nothing_is_applied_before_the_yes(world):
    out = _set()
    assert out.startswith("SETTING_NEEDS_YES"), out
    assert "off → on" in out and "Nothing has changed" in out
    assert _load_settings()["big_mode"] == "off"
    card = _card()
    assert card["status"] == "pending" and "big mode" in card["title"]
    assert "off" in card["description"] and "on" in card["description"] and "30 days" in card["description"]


def test_the_yes_applies_it_with_provenance_and_the_change_is_listed(world):
    _set()
    ap.decide_with_outcome(_card()["approval_id"], "approve", decided_by="owner")
    assert _load_settings()["big_mode"] == "on"
    rows = sp.changes("settings.accessibility.big_mode")
    assert len(rows) == 1 and rows[0]["by"] == "Friday, by a proposal you accepted"
    assert (rows[0]["old"], rows[0]["new"]) == ("off", "on") and rows[0]["undoable"] is True


def test_a_no_changes_nothing(world):
    _set()
    ap.decide_with_outcome(_card()["approval_id"], "deny", decided_by="owner")
    assert _load_settings()["big_mode"] == "off" and sp.changes() == []


def test_a_conditional_spoken_yes_does_not_apply_and_becomes_a_revision(world):
    _set()
    cid = _card()["approval_id"]
    res = local_context.decide_by_voice(cid, "yes, if it's cheaper", False, "approve")
    assert res["ok"] is False and res.get("revise") is True
    assert ap.get_approval(cid)["status"] == "pending" and _load_settings()["big_mode"] == "off"
    res = local_context.decide_by_voice(cid, "yes", False, "approve")
    assert res["ok"] is True and _load_settings()["big_mode"] == "on"


def test_undo_puts_it_back_and_only_the_newest_change_can_be_undone(world):
    _set(value="on")
    ap.decide_with_outcome(_card()["approval_id"], "approve", decided_by="owner")
    _set(value="auto")
    ap.decide_with_outcome(_card()["approval_id"], "approve", decided_by="owner")
    assert _load_settings()["big_mode"] == "auto"
    first = [r for r in sp.changes() if r["new"] == "on"][0]
    refused = sp.undo(change_id=first["id"])
    assert refused["ok"] is False and "changed again" in refused["text"] and _load_settings()["big_mode"] == "auto"
    out = _set(op="undo")
    assert out.startswith("SETTING_UNDONE") and _load_settings()["big_mode"] == "on"
    assert sp.undo(path="settings.accessibility.big_mode")["ok"] is True and _load_settings()["big_mode"] == "off"


def test_undo_holds_for_thirty_days_and_not_longer(world, monkeypatch):
    _set()
    ap.decide_with_outcome(_card()["approval_id"], "approve", decided_by="owner")
    rows = sp._read()
    rows[-1]["at"] = time.time() - 31 * 86400
    sp._write(rows)
    assert sp.changes()[0]["undoable"] is False
    res = sp.undo(path="settings.accessibility.big_mode")
    assert res["ok"] is False and "30 days" in res["text"] and _load_settings()["big_mode"] == "on"


def test_it_already_reads_that_way_raises_no_card(world):
    out = _set(value="off")
    assert out.startswith("SETTING_UNCHANGED") and _card() is None


def test_an_unknown_row_is_refused_with_the_rows_that_exist(world):
    out = _set(path="settings.privacy.cloud_consent", value="on")
    assert out.startswith("SETTING_FAIL") and "settings.accessibility.big_mode" in out and _card() is None


def test_a_bad_value_is_refused_before_a_card(world):
    out = _set(value="sideways")
    assert "big_mode error" in out and _card() is None


def test_the_tool_is_internal_ring_one_and_its_value_is_checked_for_where_it_came_from():
    assert ag.classify("set_setting", {"path": "settings.accessibility.big_mode", "value": "on"})[0] == ag.INTERNAL
    assert taint.TOOL_ROLES["set_setting"] == {"value": "instruction"}
    assert "set_setting" in agent.CLAUDE_TOOL_HANDLERS
