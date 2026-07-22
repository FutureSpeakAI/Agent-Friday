"""Restart-persistence test for services/goals.py (V6_WHOLENESS_SPEC.md §4
Phase 5 acceptance: "The goal and its receipts survive a server restart
(persistent), and appear attributed in work_log" / AUTONOMY_SPEC A3
acceptance: "goal + receipts survive a simulated restart (re-import/re-init)
and appear in work_log").

A real OS-process restart can't be exercised cheaply inside one pytest run,
so this simulates it the way the spec itself names: re-import/re-init both
modules via importlib.reload() and prove every fact about the goal — status,
milestone state, receipts, and their signatures — is read back from the
on-disk JSON, not from anything that lived only in process memory. Also
confirms goal_id shows up in work_log (a genuinely separate on-disk SQLite
store, unaffected by the reload) via goal_ancestry_json.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    from agent_friday.services import goals as goals_mod
    from agent_friday.services import approvals as approvals_mod
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(goals_mod, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals_mod, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(goals_mod, "RECEIPTS_LOG", tmp_path / "governance" / "goal_receipts.jsonl")
    monkeypatch.setattr(approvals_mod, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    yield
    goals_mod.set_executor(None)


def test_goal_and_receipts_survive_a_simulated_restart(tmp_path, monkeypatch):
    from agent_friday.services import goals
    from agent_friday.services import approvals

    goal = goals.create_goal(
        title="Ship the weekly newsletter", description="Draft and publish weekly",
        success_criteria="mentions the newsletter", status="active",
        budget_cap_mψ=5000,
        milestones=[{"name": "Draft issue 1"}, {"name": "Draft issue 2"}])
    ms1, ms2 = goal["milestones"][0]["milestone_id"], goal["milestones"][1]["milestone_id"]

    goals.set_executor(lambda g, m, *, critique_hint="":
                       {"ok": True, "text": "the newsletter draft is ready", "cost_mψ": 50})
    monkeypatch.setattr(goals.qa_gates, "evaluate_text",
                       lambda content, intent, **kw: {
                           "status": "ok", "passed": True, "score": 0.9,
                           "threshold": 0.7, "critique": "", "suggestions": ""})

    result = goals.run_milestone(goal["goal_id"], ms1)
    assert result["ok"] is True and result["status"] == "done"

    # ── Simulate a restart: re-import/re-init both modules. Reload re-runs
    # each module's top-level code (including its FRIDAY_DIR/GOALS_DIR
    # assignments), which would otherwise repoint them at the REAL ~/.friday
    # instead of this test's isolated tmp_path — so re-apply the same
    # redirection a real restart wouldn't need (a real restart keeps reading
    # the same ~/.friday; here we have to tell it where "the same disk
    # location" is). Order matters: approvals first (goals re-registers its
    # decision hooks into it), matching real dependency order.
    importlib.reload(approvals)
    importlib.reload(goals)
    monkeypatch.setattr(goals, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(goals, "RECEIPTS_LOG", tmp_path / "governance" / "goal_receipts.jsonl")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    # goals.py's `_EXECUTOR: Callable[...] = _default_executor` is a
    # module-level assignment — importlib.reload(goals) unconditionally
    # resets it back to _default_executor, silently discarding the
    # goals.set_executor(...) fake installed above (pre-reload). Without
    # re-installing the fake here, the second run_milestone call below falls
    # straight through to the REAL default executor (services.agent.
    # _spawn_task), making a live, uncontrolled Anthropic API call and real
    # tool invocations — a hermetic-suite violation that happened to pass
    # silently on a dev machine with a working key and a local Ollama.
    goals.set_executor(lambda g, m, *, critique_hint="":
                       {"ok": True, "text": "the newsletter draft is ready", "cost_mψ": 50})

    # ── Everything about the goal is read back from disk, not memory ──
    reloaded = goals.get_goal(goal["goal_id"])
    assert reloaded is not None
    assert reloaded["title"] == "Ship the weekly newsletter"
    assert reloaded["status"] == "active"  # one milestone still pending
    ms = goals._find_milestone(reloaded, ms1)
    assert ms["status"] == "done"
    assert ms["verification"]["passed"] is True

    receipts = goals.get_receipts(goal["goal_id"])
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["kind"] == "milestone_complete"
    assert goals.verify_receipt(receipt) is True  # signature still verifies post-reload

    # ── The second milestone is untouched and still runnable post-restart ──
    second = goals.run_milestone(goal["goal_id"], ms2)
    assert second["ok"] is True and second["status"] == "done"
    assert goals.get_goal(goal["goal_id"])["status"] == "completed"
    assert len(goals.get_receipts(goal["goal_id"])) == 2

    # ── goal_id appears in work_log's goal_ancestry_json (a separate,
    # genuinely on-disk SQLite store — unaffected by the Python-level
    # reload either way, but confirmed here for completeness) ──
    from agent_friday.services import work_log as wl
    entries = [e for e in wl.get_log(limit=500)
              if (e.get("goal_ancestry_json") or {}).get("goal_id") == goal["goal_id"]]
    assert len(entries) == 2
    assert all(e["status"] == "COMPLETED" for e in entries)


def test_list_goals_reads_every_file_after_reload(tmp_path, monkeypatch):
    from agent_friday.services import goals
    from agent_friday.services import approvals

    g1 = goals.create_goal(title="Goal one", status="active")
    g2 = goals.create_goal(title="Goal two", status="proposed")

    importlib.reload(approvals)
    importlib.reload(goals)
    monkeypatch.setattr(goals, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")

    all_goals = {g["goal_id"] for g in goals.list_goals()}
    assert {g1["goal_id"], g2["goal_id"]} <= all_goals
