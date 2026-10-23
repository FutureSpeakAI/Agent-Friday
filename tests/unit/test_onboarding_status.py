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


def _acks(ob, **kw):
    (ob.home / "onboarding.json").write_text(json.dumps(kw), encoding="utf-8")


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
                    journal=[{"actor": "owner", "op": "add"}])
    _acks(ob, action_place_ack=True)
    out = ob.status()
    assert out["done"] is True and out["receipt"]["seq"] == 7


def test_a_reading_tools_outcome_is_not_a_useful_action(ob):
    ob.state["outcomes"] = [{"status": "ok", "approval": None, "tool": "search_web", "_seq": 1}]
    m = _met(ob)
    assert m["action"] is False and m["receipt"] is False


def test_a_failed_action_does_not_count(ob):
    ob.state["outcomes"] = [{"status": "error", "approval": "ap-1", "tool": "send_email", "_seq": 1}]
    assert _met(ob)["action"] is False


def test_where_it_ran_needs_the_receipt_and_the_owners_ack(ob):
    _acks(ob, action_place_ack=True)
    assert _met(ob)["where_ran"] is False                      # no receipt yet
    ob.state["outcomes"] = [{"status": "ok", "approval": "ap-1", "tool": "t", "_seq": 3}]
    assert _met(ob)["where_ran"] is True


def test_friday_writing_her_own_memory_is_not_a_confirmation(ob):
    ob.state["journal"] = [{"actor": "friday", "op": "add"}]
    assert _met(ob)["memory"] is False
    ob.state["corrections"] = 1
    assert _met(ob)["memory"] is True


def test_an_explicit_skip_counts_for_the_goal(ob):
    assert _met(ob)["goal"] is False
    _acks(ob, goal_skipped=True)
    assert _met(ob)["goal"] is True


def test_a_check_that_cannot_be_read_is_not_met(ob, monkeypatch):
    from agent_friday.services import first_synthesis

    def boom():
        raise OSError("unreadable")
    monkeypatch.setattr(first_synthesis, "reviewed", boom)
    assert _met(ob)["reviewed"] is False
