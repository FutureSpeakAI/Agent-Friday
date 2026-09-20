"""A workflow step that dies must say why, where the person is looking.

THE EVENING THIS COST. On 2026-09-19 an RSI workflow's implementation step came
back "interrupted" twice. Hours went into theorising - was the local 27B not
capable enough, was the spec written for the wrong audience, did the prompt
need rewriting for a smaller model. All plausible. All wrong.

The step had been killed by a server restart, and `reconcile_tasks` wrote the
reason down BOTH TIMES: "Interrupted by a restart - this was a free-form run
and its state lived in a process that no longer exists."

Two separate failures kept that from reaching anyone:

  1. `chain_run_status` reported a step's STATUS and nothing else, so
     `workflow_status` handed the model the bare word "interrupted". The
     assistant reading it said it could not see why. That was true, and it was
     the bug.
  2. `_spawn_task` had no `conversation_id`, so `reconcile._report` fell
     through `conv.resolve(None)` to Main. The explanation was filed in a
     conversation nobody was reading.

A dead end is where invented explanations come from.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent as A
from agent_friday.services import reconcile as R


@pytest.fixture()
def tasks(monkeypatch):
    """`reconcile_tasks` imports TASKS from `core` INSIDE the function, so the
    patch has to land on core - patching the reconcile module does nothing and
    the tests silently exercise the real registry."""
    import threading

    import agent_friday.core as core
    store = {}
    monkeypatch.setattr(core, "TASKS", store, raising=False)
    monkeypatch.setattr(core, "TASKS_LOCK", threading.RLock(), raising=False)
    return store


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
