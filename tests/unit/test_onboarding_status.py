"""Onboarding is done only by North Star 9.6 (services/onboarding_status), not by .setup_complete.

Six checks, each read from a record that already exists. On a tree without the module each test
fails at its fixture.
"""
from __future__ import annotations

import importlib
import json

import pytest


def _module():
    try:
        return importlib.import_module("agent_friday.services.onboarding_status")
    except ImportError:
        pytest.fail("onboarding has no completion criterion: services/onboarding_status does not exist")


@pytest.fixture
def ob(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    mod = _module()
    from agent_friday import core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_is_existing_install", lambda: True)
    from agent_friday.services import first_synthesis, action_ledger, core_memory, goals, import_staging
    state = {"reviewed": False, "outcomes": [], "journal": [], "goals": [], "corrections": 0, "accepted": 0}
    monkeypatch.setattr(first_synthesis, "reviewed", lambda: state["reviewed"])
    monkeypatch.setattr(first_synthesis, "corrections", lambda: state["corrections"])
    monkeypatch.setattr(action_ledger, "records", lambda kind, **k: list(state["outcomes"]) if kind == "outcome" else [])
    monkeypatch.setattr(core_memory, "_read_jsonl", lambda p: list(state["journal"]))
    monkeypatch.setattr(goals, "list_goals", lambda **k: list(state["goals"]))
    monkeypatch.setattr(import_staging, "counts", lambda source=None: {"accepted": state["accepted"]})
    mod.state, mod.home = state, tmp_path
    return mod


def _answers(ob, **kw):
    d = ob.home / "first_synthesis"
    d.mkdir(parents=True, exist_ok=True)
    (d / "onboarding.json").write_text(json.dumps(kw), encoding="utf-8")


def _met(ob):
    return {c["id"]: c["met"] for c in ob.status()["checks"]}


def test_setup_complete_alone_is_not_onboarding_done(ob):
    out = ob.status()
    assert out["setup_complete"] is True and out["done"] is False
    assert [c["id"] for c in out["checks"]] == ["reviewed", "action", "receipt", "where_ran", "memory", "goal"]
    assert not any(c["met"] for c in out["checks"])


def test_all_six_make_it_done(ob):
    ob.state.update(reviewed=True, goals=[{"id": "g"}],
                    outcomes=[{"status": "ok", "approval": "ap-1", "tool": "send_email", "_seq": 7, "_ts": 1.0}],
                    journal=[{"actor": "owner", "op": "replace"}])
    _answers(ob, saw_where_it_ran_seq=7)
    out = ob.status()
    assert out["done"] is True and out["receipt"]["seq"] == 7


def test_a_reading_tools_outcome_is_not_a_useful_action(ob):
    ob.state["outcomes"] = [{"status": "ok", "approval": None, "tool": "search_web", "_seq": 1}]
    m = _met(ob)
    assert m["action"] is False and m["receipt"] is False


def test_a_failed_action_does_not_count(ob):
    ob.state["outcomes"] = [{"status": "error", "approval": "ap-1", "tool": "send_email", "_seq": 1}]
    assert _met(ob)["action"] is False


def test_where_it_ran_is_tied_to_that_receipt(ob):
    _answers(ob, saw_where_it_ran_seq=3)
    assert _met(ob)["where_ran"] is False                      # no receipt yet
    ob.state["outcomes"] = [{"status": "ok", "approval": "ap-1", "tool": "t", "_seq": 3}]
    assert _met(ob)["where_ran"] is True
    ob.state["outcomes"] = [{"status": "ok", "approval": "ap-2", "tool": "t", "_seq": 9}]
    assert _met(ob)["where_ran"] is False                      # an answer about another action


def test_the_owners_answers_are_checked(ob):
    assert ob.answer("saw_where_it_ran", seq=1)["ok"] is False   # nothing ran yet
    ob.state["outcomes"] = [{"status": "ok", "approval": "ap-1", "tool": "t", "_seq": 4}]
    assert ob.answer("saw_where_it_ran", seq=5)["ok"] is False
    assert ob.answer("saw_where_it_ran", seq=4)["ok"] is True
    assert ob.answer("skip_goal")["ok"] is True
    m = _met(ob)
    assert m["where_ran"] and m["goal"]


def test_friday_writing_her_own_memory_is_not_a_confirmation(ob):
    ob.state["journal"] = [{"actor": "friday", "op": "replace"}, {"actor": "owner", "op": "add"}]
    assert _met(ob)["memory"] is False                         # an owner add (or a migration) is not one
    ob.state["journal"].append({"actor": "owner", "op": "remove"})
    assert _met(ob)["memory"] is True
    ob.state["journal"] = []
    ob.state["corrections"] = 1
    assert _met(ob)["memory"] is True


def _first_action(ob, **state):
    import json as _json
    d = ob.home / "first_synthesis"
    d.mkdir(parents=True, exist_ok=True)
    (d / "first_action.json").write_text(_json.dumps(state), encoding="utf-8")


def test_a_recorded_skip_of_the_first_action_lets_onboarding_be_done(ob):
    ob.state.update(reviewed=True, goals=[{"id": "g"}], journal=[{"actor": "owner", "op": "replace"}])
    assert ob.status()["done"] is False
    _first_action(ob, state="skipped", skipped_at=5.0)
    out = ob.status()
    assert out["done"] is True
    marks = {c["id"]: c for c in out["checks"]}
    for k in ("action", "receipt", "where_ran"):
        assert marks[k]["met"] and marks[k]["skipped"], k
    assert out["first_action"]["state"] == "skipped"


def test_a_proposal_waiting_for_the_press_meets_nothing(ob):
    _first_action(ob, state="proposed", approval_id="ap-1")
    m = _met(ob)
    assert not m["action"] and not m["receipt"] and not m["where_ran"]


def test_the_first_actions_own_receipt_meets_action_and_receipt(ob):
    _first_action(ob, state="done", receipt_seq=12)
    out = ob.status()
    m = {c["id"]: c for c in out["checks"]}
    assert m["action"]["met"] and m["receipt"]["met"] and not m["action"]["skipped"]
    assert out["receipt"]["seq"] == 12 and out["receipt"]["tool"] == "first_action_reminder"
    assert m["where_ran"]["met"] is False                    # the owner has not said it yet
    assert ob.answer("saw_where_it_ran", seq=12)["ok"] is True
    assert _met(ob)["where_ran"] is True


def test_skipping_the_first_action_is_an_owner_answer(ob, monkeypatch):
    from agent_friday.services import first_action
    calls = []
    monkeypatch.setattr(first_action, "skip", lambda by="owner:ui": calls.append(by) or {"ok": True, "state": "skipped"})
    assert ob.answer("skip_first_action")["ok"] is True
    assert calls == ["owner:ui"]


def test_an_explicit_skip_counts_for_the_goal(ob):
    assert _met(ob)["goal"] is False
    _answers(ob, goal_skipped_ts=1.0)
    assert _met(ob)["goal"] is True


def test_a_check_that_cannot_be_read_is_not_met(ob, monkeypatch):
    from agent_friday.services import first_synthesis

    def boom():
        raise OSError("unreadable")
    monkeypatch.setattr(first_synthesis, "reviewed", boom)
    assert _met(ob)["reviewed"] is False
