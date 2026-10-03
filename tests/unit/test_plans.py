"""Plan-first for big asks (docs/design/active/vibe-coding-salon.md §4.11 item 4).

A plan is a `markdown` artifact whose metadata carries milestones. It is
built only after the user approves it, in the panel or in their own words;
milestones become task-ledger entries; and every step closes with one of the
five typed blockers the goals spec names, or none.
"""
from __future__ import annotations

import pytest

from agent_friday.services import artifacts as art
from agent_friday.services import plans

CID = "conv-plan-test"


@pytest.fixture(autouse=True)
def _store(monkeypatch, tmp_path):
    monkeypatch.setattr(art, "_root", lambda: tmp_path / "artifacts")
    yield


def test_a_plan_is_a_markdown_artifact_with_milestones_awaiting_approval():
    rec = plans.create(CID, "Rent tracker", "# Plan\n\n1. Page\n2. Storage\n", ["Make the page", "Store rows", "Add a chart"])
    assert rec["kind"] == "markdown" and rec["title"] == "Rent tracker"
    p = rec["meta"]["plan"]
    assert p["approved"] is False and p["approved_at"] is None
    assert [m["title"] for m in p["milestones"]] == ["Make the page", "Store rows", "Add a chart"]
    assert all(m["status"] == "todo" and m["n"] == i + 1 for i, m in enumerate(p["milestones"]))
    assert plans.current(CID)["id"] == rec["id"]
    assert art.get(CID, rec["id"])["content"].startswith("# Plan")


def test_a_plan_needs_at_least_one_milestone_and_at_most_twelve():
    with pytest.raises(ValueError):
        plans.create(CID, "x", "# p", [])
    with pytest.raises(ValueError):
        plans.create(CID, "x", "# p", ["m%d" % i for i in range(13)])


def test_approval_is_a_new_version_that_keeps_the_text_and_opens_a_ledger():
    rec = plans.create(CID, "Tracker", "# Plan\n", ["One", "Two"])
    ok = plans.approve(CID, rec["id"], by="you")
    assert ok["version"] == 2 and ok["content"] == "# Plan\n"
    p = ok["meta"]["plan"]
    assert p["approved"] is True and p["approved_at"] and p["approved_by"] == "you"
    assert p["task_id"], "milestones become a task-ledger run"
    from agent_friday.services import task_ledger
    led = task_ledger.load(p["task_id"])
    assert led and "Tracker" in (led.get("goal") or "")
    # Approving twice is one approval.
    again = plans.approve(CID, rec["id"], by="you")
    assert again["version"] == 2


def test_milestones_move_and_close_with_a_typed_blocker():
    rec = plans.create(CID, "Tracker", "# Plan\n", ["One", "Two"])
    plans.approve(CID, rec["id"])
    r = plans.milestone(CID, rec["id"], 1, "doing")
    assert r["meta"]["plan"]["milestones"][0]["status"] == "doing"
    r = plans.milestone(CID, rec["id"], 1, "done", step="abc1234")
    m = r["meta"]["plan"]["milestones"][0]
    assert m["status"] == "done" and m["step"] == "abc1234" and m["blocker"] is None
    r = plans.milestone(CID, rec["id"], 2, "blocked", blocker="needs_user_input", note="which currency?")
    m = r["meta"]["plan"]["milestones"][1]
    assert m["status"] == "blocked" and m["blocker"] == "needs_user_input" and m["note"] == "which currency?"
    with pytest.raises(ValueError):
        plans.milestone(CID, rec["id"], 2, "blocked", blocker="vibes")
    with pytest.raises(ValueError):
        plans.milestone(CID, rec["id"], 9, "done")
    with pytest.raises(ValueError):
        plans.milestone(CID, rec["id"], 1, "finished")


def test_the_five_blockers_are_the_goals_specs():
    assert set(plans.BLOCKERS) == {"missing_evidence", "needs_user_input", "run_failed", "external_wait", "goal_not_met_yet"}


def test_a_milestone_cannot_move_before_approval():
    rec = plans.create(CID, "Tracker", "# Plan\n", ["One"])
    with pytest.raises(plans.NotApproved):
        plans.milestone(CID, rec["id"], 1, "doing")


def test_the_model_is_told_to_wait_then_told_what_to_build_next_then_that_it_is_done():
    rec = plans.create(CID, "Tracker", "# Plan\n", ["Make the page", "Store rows"])
    b = plans.context_block(CID)
    assert plans.CONTEXT_HEADER in b and rec["id"] in b
    assert "awaiting" in b.lower() and "do not build" in b.lower() and "plan_approve" in b
    plans.approve(CID, rec["id"])
    b = plans.context_block(CID)
    assert "approved" in b.lower() and "next: milestone 1" in b.lower() and "Make the page" in b
    assert "plan_milestone" in b
    plans.milestone(CID, rec["id"], 1, "done", step="a1b2c3d")
    b = plans.context_block(CID)
    assert "next: milestone 2" in b.lower() and "a1b2c3d" in b
    plans.milestone(CID, rec["id"], 2, "blocked", blocker="needs_user_input", note="which currency?")
    b = plans.context_block(CID)
    assert "needs_user_input" in b and "which currency?" in b
    plans.milestone(CID, rec["id"], 2, "done")
    b = plans.context_block(CID)
    assert "complete" in b.lower()


def test_no_plan_means_no_block():
    assert plans.context_block(CID) == ""
    assert plans.current(CID) is None


def test_a_hand_edit_to_the_plan_text_keeps_the_milestones():
    rec = plans.create(CID, "Tracker", "# Plan\n", ["One", "Two"])
    art.edit(CID, rec["id"], "# Plan, edited\n")
    cur = plans.current(CID)
    assert cur["content"] == "# Plan, edited\n"
    assert [m["title"] for m in cur["meta"]["plan"]["milestones"]] == ["One", "Two"]
