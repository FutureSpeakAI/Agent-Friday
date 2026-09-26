"""A task started from a chat reports back into that chat, cards included.

From the 5.14.2 walkthrough. Approving a card raised by a BACKGROUND TASK runs
the action -- that much was verified -- and the result never reached the chat. The
executor posts its outcome into `payload["conversation_id"]`, the taint gate takes
that from the session context of the call that raised the card, and a task's
session context never had one.

Everything needed was already there and unused:

  * `_spawn_task(conversation_id=...)` exists, and its own docstring says why:
    "WHERE THIS TASK REPORTS. Without it a task belongs to nobody."
  * `_CURRENT_CONVERSATION` is set by `_execute_tool` for exactly this, and
    `_tool_start_workflow` already reads it.

`_tool_spawn_task` simply never passed it, and the worker never put it back into
the session context its tool calls run under. So a card raised inside a task
carried `conversation_id: ""`, the executor had nowhere to post, and the docked
chat had nothing to refresh.
"""

import json

import pytest


# ── the tool hands the conversation to the task ────────────────────────────

def test_spawn_task_passes_the_originating_conversation(monkeypatch):
    from agent_friday.services import agent as ag
    seen = {}

    def fake_spawn(name, prompt, description='', **kw):
        seen.update(kw)
        seen["name"] = name
        return "task-1"

    monkeypatch.setattr(ag, "_spawn_task", fake_spawn)
    tok = ag._CURRENT_CONVERSATION.set("conv-abc123")
    try:
        out = ag._tool_spawn_task({"name": "Look into it", "prompt": "do the thing"})
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)
    assert json.loads(out)["status"] == "running"
    assert seen.get("conversation_id") == "conv-abc123", seen


def test_no_conversation_is_passed_as_none_not_empty(monkeypatch):
    """A task with no originating chat must stay None, which is what
    `reconcile.resolve` reads as 'file it in Main'."""
    from agent_friday.services import agent as ag
    seen = {}
    monkeypatch.setattr(ag, "_spawn_task",
                        lambda n, p, d='', **kw: seen.update(kw) or "task-2")
    tok = ag._CURRENT_CONVERSATION.set(None)
    try:
        ag._tool_spawn_task({"name": "x", "prompt": "y"})
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)
    assert seen.get("conversation_id") is None, seen


# ── the running task carries it into every tool call ───────────────────────

def test_the_worker_session_context_carries_the_conversation(monkeypatch):
    """The session context a task's tool calls run under is where the taint
    gate looks when it stamps a card."""
    from agent_friday.services import agent as ag
    monkeypatch.setitem(ag.TASKS, "task-9",
                        {"conversation_id": "conv-xyz789", "schedule_id": None})
    assert ag._task_conversation_id("task-9") == "conv-xyz789"


def test_an_unknown_task_has_no_conversation(monkeypatch):
    from agent_friday.services import agent as ag
    assert ag._task_conversation_id("task-does-not-exist") is None


def test_the_worker_builds_a_context_with_the_conversation():
    """Both places the worker builds a session context must include it; a card
    raised from the steer leg posts back exactly like one from the main leg."""
    import inspect
    from agent_friday.services import agent as ag
    src = inspect.getsource(ag)
    start = src.index("def _task_worker_untraced")
    body = src[start:start + 40000]
    ctxs = body.count('"is_background_task": True')
    withconv = body.count('"conversation_id": _task_conversation_id(task_id)')
    assert ctxs >= 2, "expected at least two worker session contexts, saw %d" % ctxs
    assert withconv == ctxs, (
        "%d of %d worker session contexts carry the conversation" % (withconv, ctxs))


# ── end to end: a card raised in a task knows where to report ──────────────

def test_a_card_raised_in_a_task_carries_the_conversation(tmp_path, monkeypatch):
    """The reported failure, at the seam that caused it."""
    from agent_friday.services import approvals as ap
    from agent_friday.services import agent as ag
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "FRIDAY_DIR", tmp_path)

    session_ctx = {"authenticated": True, "is_background_task": True,
                   "task_id": "task-7", "conversation_id": "conv-task-7"}
    rec = ap.create_approval(
        kind="tainted_action", subject_type="tool_action",
        subject_id="taint:task:1", title="Write a file",
        action_description='write_file {"path": "x"}', force_gate=True,
        payload={"tool": "write_file", "input": {"path": "x"},
                 "conversation_id": (session_ctx or {}).get("conversation_id") or ""},
        action_class=ag._gate_policy_class("write_file", {"path": "x"}),
        requested_by="taint_gate")
    assert rec["payload"]["conversation_id"] == "conv-task-7"


def test_the_executor_posts_a_task_card_result_into_that_conversation(tmp_path,
                                                                     monkeypatch):
    from agent_friday.services import approvals as ap
    from agent_friday.services import approval_executor as ex
    from agent_friday.services import conversations as convs
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations",
                        raising=False)
    monkeypatch.setattr(ex, "_run_tool", lambda n, a, **k: "done")
    ex.register()

    conv = convs.create(title="Task thread")
    cid = conv["id"] if isinstance(conv, dict) else conv
    rec = ap.create_approval(
        kind="tainted_action", subject_type="tool_action",
        subject_id="taint:task:2", title="Write a file",
        action_description='write_file {"path": "x"}', force_gate=True,
        payload={"tool": "write_file", "input": {"path": "x"},
                 "conversation_id": cid},
        requested_by="taint_gate")
    ap.decide(rec["approval_id"], "approve", decided_by="owner")
    msgs = convs.messages(cid)
    assert msgs, "a task's approved card still reports nowhere"
    assert "write_file" in " ".join((m.get("text") or "") for m in msgs)
