"""A workflow step that dies must say why, where the person is looking.

A workflow step that comes back as a bare "interrupted" invites hours of
plausible, wrong theories (is the local model capable enough, is the spec
written for the wrong audience, does the prompt need rewriting). When a
server restart kills a step, `reconcile_tasks` records the reason:
"Interrupted by a restart - this was a free-form run and its state lived in a
process that no longer exists." That reason has to reach the person:

  1. `chain_run_status` reports the reason, not only the STATUS, so
     `workflow_status` does not hand the model the bare word "interrupted"
     and leave it unable to say why.
  2. `_spawn_task` carries a `conversation_id`, so `reconcile._report` does
     not fall through `conv.resolve(None)` to Main and file the explanation
     in a conversation nobody is reading.

A dead end is where invented explanations come from.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent as A
from agent_friday.services import reconcile as R


@pytest.fixture()
def tasks():
    """The live task ledger, emptied around the test.

    Do not `monkeypatch.setattr(core, "TASKS", store, raising=False)`:

      * TASKS and TASKS_LOCK live in `services.agent`, never in `core`.
      * `raising=False` on a name that does not exist CREATES it. Such a
        fixture manufactures `core.TASKS` moments before a function imports
        it, the import succeeds, and the tests pass - inside a world that
        exists only while they run.
      * In production there is no fixture. A `reconcile_tasks` importing from
        `core` raises ImportError on every boot, swallows it and returns
        `{"interrupted": []}`, and the function whose whole purpose is "a job
        that stopped must say it stopped" never marks a single task.

    services/reconcile imports from services.agent. Patching where production
    actually reads means these tests exercise the real registry, which is
    also why the fixture has to clear it rather than swap it.
    """
    from agent_friday.services.agent import TASKS, TASKS_LOCK
    with TASKS_LOCK:
        saved = dict(TASKS)
        TASKS.clear()
    try:
        yield TASKS
    finally:
        with TASKS_LOCK:
            TASKS.clear()
            TASKS.update(saved)


# ── the reason is written where the status is ───────────────────────────────

def test_an_interrupted_task_records_why(tasks, monkeypatch):
    monkeypatch.setattr(R, "_report", lambda *a, **k: None)
    tasks["t1"] = {"task_id": "t1", "status": "running", "name": "step 2"}
    R.reconcile_tasks()
    assert tasks["t1"]["status"] == "interrupted"
    assert "restart" in (tasks["t1"].get("status_reason") or "").lower(), \
        "the status carries no cause, so a reader can only guess"


def test_a_finished_task_is_left_alone(tasks, monkeypatch):
    monkeypatch.setattr(R, "_report", lambda *a, **k: None)
    tasks["t1"] = {"task_id": "t1", "status": "complete", "name": "done"}
    R.reconcile_tasks()
    assert tasks["t1"]["status"] == "complete"
    assert "status_reason" not in tasks["t1"]


# ── the reason reaches the person ───────────────────────────────────────────

def test_the_notice_goes_to_the_conversation_that_started_it(tasks, monkeypatch):
    """THE CRUX. Without this the explanation lands in Main, and the person
    watching a different chat is told only that something was interrupted."""
    sent = []
    monkeypatch.setattr(R, "_report",
                        lambda cid, text, meta=None: sent.append((cid, text)))
    tasks["t1"] = {"task_id": "t1", "status": "running", "name": "step 2",
                   "conversation_id": "conv-abc"}
    R.reconcile_tasks()
    assert sent and sent[0][0] == "conv-abc"
    assert "restart" in sent[0][1].lower()


def test_a_task_spawned_from_a_tool_carries_its_conversation(monkeypatch):
    """The plumbing that makes the above possible: a tool call knows which
    conversation it belongs to, and hands that to the work it starts."""
    seen = {}

    def _fake_spawn(**kw):
        seen.update(kw)
        return "task-1"

    monkeypatch.setattr(A, "_spawn_task", _fake_spawn)
    monkeypatch.setattr(A, "load_workflow_chain",
                        lambda n: {"name": n, "slug": n,
                                   "steps": [{"name": "s1", "prompt": "go"}]})
    tok = A._CURRENT_CONVERSATION.set("conv-xyz")
    try:
        A.run_workflow_chain("demo", conversation_id=A._CURRENT_CONVERSATION.get())
    finally:
        A._CURRENT_CONVERSATION.reset(tok)
    assert seen.get("conversation_id") == "conv-xyz"


def test_without_a_conversation_nothing_breaks(monkeypatch):
    """Voice, channels and the scheduler predate conversations. They must keep
    working - they simply report to Main, as they always have."""
    seen = {}
    monkeypatch.setattr(A, "_spawn_task", lambda **kw: seen.update(kw) or "t")
    monkeypatch.setattr(A, "load_workflow_chain",
                        lambda n: {"name": n, "slug": n,
                                   "steps": [{"name": "s1", "prompt": "go"}]})
    A.run_workflow_chain("demo")
    assert seen.get("conversation_id") is None


# ── the status tool actually shows it ───────────────────────────────────────

def test_workflow_status_prints_the_reason(monkeypatch):
    monkeypatch.setattr(A, "chain_run_status", lambda name: {
        "name": "rsi", "slug": "rsi", "state": "failed",
        "steps": [
            {"index": 0, "name": "read-spec", "status": "completed",
             "reason": None, "result_tail": ""},
            {"index": 1, "name": "implement", "status": "interrupted",
             "reason": "Interrupted by a restart — this was a free-form run.",
             "result_tail": ""},
        ]})
    out = A._tool_workflow_status({"name": "rsi"})
    assert "interrupted" in out
    assert "reason:" in out and "restart" in out, \
        "the model is still being handed a bare status word"


def test_a_completed_step_needs_no_excuse(monkeypatch):
    monkeypatch.setattr(A, "chain_run_status", lambda name: {
        "name": "x", "slug": "x", "state": "completed",
        "steps": [{"index": 0, "name": "a", "status": "completed",
                   "reason": None, "result_tail": ""}]})
    out = A._tool_workflow_status({"name": "x"})
    assert "reason:" not in out
