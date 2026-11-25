"""Several wiki edits from one request are one tray card, and each edit stays its own approval underneath.

A request that edits the wiki several times used to raise one card per edit (taint cards never batched). Internal
wiki edits that use details from outside content, raised by one task in one conversation, now form one batch:
approving it applies every edit through the same gate as a single card (one decision, one run, per card), and
rejecting one edit first still applies the rest. Anything outward, any other tool, and any other task stays its own
card. Synthetic text only.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import approval_batches as ab
from agent_friday.services import approval_executor as ex
from agent_friday.services import approvals

CONV, TASK = "conv-synthetic", "task-synthetic"


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch, friday_dir):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    ex.register()
    yield


@pytest.fixture
def ran(monkeypatch):
    """Every tool run the executor performs, in order."""
    calls = []
    monkeypatch.setattr(ex, "_run_tool", lambda name, args, **kw: calls.append((name, args)) or "done")
    return calls


def _inputs(i, tool):
    if tool == "correct_wiki":
        return {"old_text": "old synthetic line %d" % i, "new_text": "new synthetic line %d" % i}
    return {"file": "entities/synthetic-%d.md" % i, "mode": "append", "new_value": "synthetic value %d" % i,
            "reason": "synthetic reason"}


def _edit(i, tool="correct_wiki", task=TASK, conv=CONV, cls="internal", **payload):
    inp = _inputs(i, tool)
    card = approvals.create_approval(
        kind="tainted_action", subject_type="tool_action", subject_id="taint:%s:%s:%d" % (conv, tool, i),
        title="Edit the wiki (%d)" % i, action_description="%s %s" % (tool, json.dumps(inp)),
        description="a detail came from something Friday read", force_gate=True, action_class=cls,
        payload=dict({"tool": tool, "input": inp, "conversation_id": conv, "task_id": task}, **payload),
        requested_by="taint_gate")
    approvals._patch(card["approval_id"], policy_class=cls)
    return approvals.get_approval(card["approval_id"])


def _burst(n=5, **kw):
    """The shape of the real burst: three corrections and two page updates from one task."""
    tools = ["correct_wiki", "correct_wiki", "correct_wiki", "propose_wiki_update", "propose_wiki_update"]
    return [_edit(i, tools[i % len(tools)], **kw) for i in range(n)]


def _ids(cards):
    return [c["approval_id"] for c in cards]


def _status(aid):
    return approvals.get_approval(aid)["status"]


# ── what batches ───────────────────────────────────────────────────────────────────────────────────────────────

def test_five_wiki_edits_from_one_request_are_one_card():
    cards = _burst()
    plan = ab.pending_plan()
    assert plan["singles"] == [] and len(plan["batches"]) == 1
    batch = plan["batches"][0]
    assert batch["count"] == 5 and sorted(batch["approval_ids"]) == sorted(_ids(cards))
    assert "wiki edits" in batch["sentence"] and "outside content" in batch["sentence"]


def test_each_item_carries_the_whole_card_so_the_page_can_mark_the_parts_from_outside_text():
    cards = _burst(2)
    approvals._patch(cards[0]["approval_id"], provenance={"headline": "Check this", "flags": [
        {"arg": "new_text", "role": "memory_write", "value": "new synthetic line 0", "source": "a web page",
         "origin": "content", "severity": "warn", "text": "from a web page"}]})
    item = next(i for i in ab.pending_plan()["batches"][0]["items"] if i["approval_id"] == cards[0]["approval_id"])
    assert item["provenance"]["flags"][0]["value"] == "new synthetic line 0"
    assert item["provenance"]["flags"][0]["source"] == "a web page"
    assert item["action_description"] == approvals.get_approval(cards[0]["approval_id"])["action_description"]


def test_two_requests_do_not_share_a_card_and_a_lone_edit_stays_single():
    first = [_edit(i, task="task-a") for i in range(2)]
    second = [_edit(10 + i, task="task-b") for i in range(2)]
    lone = _edit(20, task="task-c")
    plan = ab.pending_plan()
    assert sorted(b["count"] for b in plan["batches"]) == [2, 2]
    assert [r["approval_id"] for r in plan["singles"]] == [lone["approval_id"]]
    assert {frozenset(b["approval_ids"]) for b in plan["batches"]} == {frozenset(_ids(first)), frozenset(_ids(second))}


def test_only_internal_wiki_edits_batch_never_another_tool_or_an_outward_class():
    for i in range(3):
        approvals.create_approval(
            kind="tainted_action", subject_type="tool_action", subject_id="taint:other:%d" % i, title="Open a page",
            action_description="open_url {}", description="x", force_gate=True, action_class="internal",
            payload={"tool": "open_url", "input": {}, "conversation_id": CONV, "task_id": TASK},
            requested_by="taint_gate")
    assert ab.pending_plan()["batches"] == [], "another tool batched"
    for i in range(3):
        _edit(50 + i, cls="external_message")
    assert ab.pending_plan()["batches"] == [], "an outward class batched"
    assert "tainted_action" in ab.BATCHABLE_KINDS
    assert ab.BATCHABLE_TAINTED_TOOLS == {"correct_wiki", "propose_wiki_update"}


def test_an_edit_with_no_conversation_or_task_never_joins_a_batch():
    cards = [_edit(i, task="", conv="") for i in range(3)]
    assert ab.pending_plan()["batches"] == [] and len(ab.pending_plan()["singles"]) == 3
    assert all(_status(c["approval_id"]) == "pending" for c in cards)


# ── deciding the batch ─────────────────────────────────────────────────────────────────────────────────────────

def test_approving_the_one_card_applies_every_edit_exactly_once(ran):
    cards = _burst()
    batch = ab.pending_plan()["batches"][0]
    out = ab.decide_batch(batch["batch_id"], batch["approval_ids"], "approve", decided_by="owner")
    assert out["ok"] and out["decided"] == 5 and out["refused"] == 0
    assert sorted(json.dumps(args, sort_keys=True) for _, args in ran) == sorted(
        json.dumps(_inputs(i, c["payload"]["tool"]), sort_keys=True) for i, c in enumerate(cards))
    assert len(ran) == 5, "an edit ran twice or not at all"
    for c in cards:                                        # one decision per card, each consumed once
        stored = approvals.get_approval(c["approval_id"])
        assert stored["status"] == "approved" and stored.get("consumed") is True


def test_rejecting_one_edit_first_still_applies_the_rest(ran):
    cards = _burst()
    rejected = cards[2]
    rec, won = approvals.decide_with_outcome(rejected["approval_id"], "deny", decided_by="owner")
    assert won and rec["status"] == "denied"
    plan = ab.pending_plan()
    assert len(plan["batches"]) == 1 and plan["batches"][0]["count"] == 4
    assert rejected["approval_id"] not in plan["batches"][0]["approval_ids"]
    batch = plan["batches"][0]
    out = ab.decide_batch(batch["batch_id"], batch["approval_ids"], "approve", decided_by="owner")
    assert out["ok"] and out["decided"] == 4
    assert len(ran) == 4 and json.dumps(_inputs(2, "correct_wiki"), sort_keys=True) not in [
        json.dumps(a, sort_keys=True) for _, a in ran], "the rejected edit ran"
    assert _status(rejected["approval_id"]) == "denied"
    assert all(_status(c["approval_id"]) == "approved" for c in cards if c is not rejected)


def test_declining_the_card_declines_every_edit_and_runs_none(ran):
    cards = _burst()
    batch = ab.pending_plan()["batches"][0]
    out = ab.decide_batch(batch["batch_id"], batch["approval_ids"], "deny", decided_by="owner")
    assert out["ok"] and out["decided"] == 5 and ran == []
    assert all(_status(c["approval_id"]) == "denied" for c in cards)


def test_an_edit_added_after_the_card_was_drawn_decides_nothing():
    _burst()
    batch = ab.pending_plan()["batches"][0]
    _edit(99)                                              # the request raised one more edit meanwhile
    out = ab.decide_batch(batch["batch_id"], batch["approval_ids"], "approve", decided_by="owner")
    assert out["ok"] is False and out["error"] == "changed"
    assert all(r["status"] == "pending" for r in approvals.list_approvals(status="pending"))
