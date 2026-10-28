"""A task whose approval card is declined, expires or is taken back gets a TYPED blocker, the
next step of its workflow says what stopped it, and the card is never used again (NS-32.8-1).

Before: the task became 'declined' with only a free-text summary; denied and expired could not be
told apart, the step that never ran had no typed reason, and a card taken back when the owner
revoked authority closed its task only at the next five-minute sweep. Synthetic tasks and cards.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent


@pytest.fixture
def task(monkeypatch):
    tid = "task-blocker-1"
    with agent.TASKS_LOCK:
        agent.TASKS[tid] = {"task_id": tid, "name": "Send the summary", "status": "waiting_approval",
                            "log": []}
    from agent_friday.services import notice_registry as nreg
    monkeypatch.setattr(nreg, "deliver_completion", lambda cid, text=None, **kw: None)
    monkeypatch.setattr(agent, "_advance_task_chain", lambda task_id, result: None)
    yield tid
    with agent.TASKS_LOCK:
        agent.TASKS.pop(tid, None)


def _cards(monkeypatch, rows):
    from agent_friday.services import approvals
    monkeypatch.setattr(approvals, "list_approvals", lambda *a, **k: [dict(r) for r in rows])


def _card(status, *, task_id="task-blocker-1", tool="send_email", **extra):
    return dict({"approval_id": "ap-" + status, "kind": "tainted_action", "status": status,
                 "payload": {"task_id": task_id, "tool": tool}}, **extra)


@pytest.mark.parametrize("card,kind,words", [
    (_card("denied"), "approval_denied", "You declined"),
    (_card("expired"), "approval_expired", "expired before you decided"),
    (_card("blocked"), "approval_blocked", "safety rule"),
    (_card("expired", withdrawn=True), "approval_withdrawn", "revoked"),
], ids=["denied", "expired", "blocked", "withdrawn"])
def test_a_task_whose_card_was_not_approved_gets_a_typed_blocker(task, monkeypatch, card, kind, words):
    _cards(monkeypatch, [card])
    assert agent.settle_waiting_task(task) == "declined"
    with agent.TASKS_LOCK:
        rec = dict(agent.TASKS[task])
    b = rec.get("blocker") or {}
    assert b.get("type") == kind and b.get("category") == "policy_blocked", b
    assert card["approval_id"] in (b.get("approval_ids") or [])
    assert words in b.get("text", "") and "new card" in b.get("text", "")
    assert rec.get("status_reason") == b.get("text")


def test_the_step_that_never_ran_says_which_card_stopped_it(task, monkeypatch):
    slug = "synthetic-two-step"
    chain = {"slug": slug, "steps": [{"name": "draft"}, {"name": "send"}]}
    monkeypatch.setattr(agent, "load_workflow_chain", lambda name: chain)
    monkeypatch.setattr(agent, "_report_task_completion", lambda *a, **k: None)
    with agent.TASKS_LOCK:
        agent.TASKS[task].update(chain=slug, chain_step=0, run_id="run-1", created=1.0)
    _cards(monkeypatch, [_card("expired")])
    agent.settle_waiting_task(task)
    view = agent.chain_run_status(slug, run_id="run-1")
    assert view and view["state"] == "stopped"
    steps = view["steps"]
    assert (steps[0].get("blocker") or {}).get("type") == "approval_expired"
    assert steps[1]["status"] == "skipped"
    assert steps[1].get("blocked_by") == {"task_id": task, "type": "approval_expired", "step": 0}
    assert "expired" in steps[1]["reason"]


def test_a_card_taken_back_closes_its_task_at_once(task, monkeypatch, tmp_path):
    """Revoking Friday's authority takes back approved, unused cards; what waited on them is told
    now, not at the next sweep."""
    from agent_friday.services import approvals
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    fired = []
    monkeypatch.setattr(approvals, "_HOOKS", {"tainted_action": [lambda rec: fired.append(rec)]})
    rec = approvals.create_approval(kind="tainted_action", subject_type="tool_action",
                                    subject_id="taint:synthetic", title="Send the summary",
                                    action_description="send it", force_gate=True,
                                    payload={"task_id": task, "tool": "send_email"})
    approvals._patch(rec["approval_id"], status="approved")
    assert approvals.withdraw_unused_approved(by="authority", note="revoked") == 1
    assert [r["approval_id"] for r in fired] == [rec["approval_id"]], "the task was not told"
    assert fired[0]["status"] == "expired" and fired[0].get("withdrawn") is True
    assert approvals.claim_for_execution(rec["approval_id"]) is False, "a taken-back card ran"
